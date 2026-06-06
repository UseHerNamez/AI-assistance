from __future__ import annotations

import os
import re
import subprocess
import sys
import time
import urllib.parse
import webbrowser
from functools import lru_cache
from pathlib import Path
from typing import Optional

if sys.platform == "win32":
    import winreg

KNOWN_SITES: dict[str, str] = {
    "youtube": "https://www.youtube.com",
    "google": "https://www.google.com",
    "gmail": "https://mail.google.com",
    "facebook": "https://www.facebook.com",
    "instagram": "https://www.instagram.com",
    "twitter": "https://twitter.com",
    "x": "https://x.com",
    "reddit": "https://www.reddit.com",
    "github": "https://github.com",
    "netflix": "https://www.netflix.com",
    "spotify": "https://open.spotify.com",
    "amazon": "https://www.amazon.com",
    "linkedin": "https://www.linkedin.com",
    "twitch": "https://www.twitch.tv",
    "discord": "https://discord.com/app",
    "outlook web": "https://outlook.live.com",
    "outlook online": "https://outlook.live.com",
    "wikipedia": "https://www.wikipedia.org",
    "bing": "https://www.bing.com",
    "duckduckgo": "https://duckduckgo.com",
}

# Prefer opening in the browser (not as a desktop app).
_URL_FIRST_TARGETS = frozenset(
    {
        "youtube",
        "google",
        "gmail",
        "facebook",
        "instagram",
        "twitter",
        "x",
        "reddit",
        "github",
        "netflix",
        "amazon",
        "linkedin",
        "twitch",
        "wikipedia",
        "bing",
        "duckduckgo",
    }
)

APP_ALIASES: dict[str, str] = {
    "notepad": "notepad.exe",
    "calculator": "calc.exe",
    "calc": "calc.exe",
    "paint": "mspaint.exe",
    "word": "WINWORD.EXE",
    "excel": "EXCEL.EXE",
    "powerpoint": "POWERPNT.EXE",
    "outlook": "OUTLOOK.EXE",
    "teams": "Teams.exe",
    "chrome": "chrome.exe",
    "firefox": "firefox.exe",
    "edge": "msedge.exe",
    "spotify": "Spotify.exe",
    "steam": "steam.exe",
    "vscode": "Code.exe",
    "visual studio code": "Code.exe",
    "vs code": "Code.exe",
    "league of legends": "LeagueClient.exe",
    "lol": "LeagueClient.exe",
    "riot client": "RiotClientServices.exe",
    "riot": "RiotClientServices.exe",
    "epic games": "EpicGamesLauncher.exe",
    "epic games launcher": "EpicGamesLauncher.exe",
    "battle net": "Battle.net.exe",
    "battlenet": "Battle.net.exe",
    "blizzard": "Battle.net.exe",
    "obs": "obs64.exe",
    "zoom": "Zoom.exe",
    "slack": "slack.exe",
    "telegram": "Telegram.exe",
    "whatsapp": "WhatsApp.exe",
}

# Microsoft Office desktop apps launch more reliably via Start Menu shortcuts.
_OFFICE_EXES = frozenset(
    {
        "OUTLOOK.EXE",
        "WINWORD.EXE",
        "EXCEL.EXE",
        "POWERPNT.EXE",
        "MSACCESS.EXE",
        "ONENOTE.EXE",
        "Teams.exe",
    }
)

# Friendly names → App Paths registry executable (desktop Office).
_OFFICE_APP_EXES: dict[str, str] = {
    "word": "WINWORD.EXE",
    "microsoft word": "WINWORD.EXE",
    "excel": "EXCEL.EXE",
    "microsoft excel": "EXCEL.EXE",
    "powerpoint": "POWERPNT.EXE",
    "power point": "POWERPNT.EXE",
    "microsoft powerpoint": "POWERPNT.EXE",
}

_RE_DOMAIN = re.compile(
    r"^[a-z0-9][\w-]*(?:\.[a-z0-9][\w-]*)+(?:/|$)",
    re.IGNORECASE,
)
_RE_TLD = re.compile(r"\.(?:com|org|net|io|co|uk|dev|app|tv|me)(?:/|$)", re.IGNORECASE)


def normalize_label(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (text or "").lower())


# Common speech-to-text variants for open targets.
_OPEN_TARGET_ALIASES: dict[str, str] = {
    "youtube": "youtube",
    "youtub": "youtube",
    "utube": "youtube",
    "youtubee": "youtube",
    "you tube": "youtube",
    "u tube": "youtube",
    "you-tube": "youtube",
    "reddit": "reddit",
    "read it": "reddit",
    "readit": "reddit",
    "google": "google",
    "gmail": "gmail",
    "g mail": "gmail",
    "facebook": "facebook",
    "face book": "facebook",
    "instagram": "instagram",
    "insta gram": "instagram",
    "twitter": "twitter",
    "netflix": "netflix",
    "spotify": "spotify",
    "github": "github",
    "git hub": "github",
    "outlook": "outlook",
    "microsoft outlook": "outlook",
    "out look": "outlook",
    "all look": "outlook",
    "auto look": "outlook",
    "outlooks": "outlook",
    "league of legends": "league of legends",
    "lol": "league of legends",
}


def canonical_open_target(target: str) -> str:
    """Normalize open-target names from casual speech / ASR."""
    raw = re.sub(r"\s+", " ", (target or "").strip().lower())
    raw = re.sub(r"^(?:the|my|a|an)\s+", "", raw).strip()
    if not raw:
        return ""
    if raw in _OPEN_TARGET_ALIASES:
        return _OPEN_TARGET_ALIASES[raw]
    collapsed = re.sub(r"[^a-z0-9]", "", raw)
    if collapsed in {"alllook", "autolook", "outlooks"}:
        return "outlook"
    for alias, canonical in _OPEN_TARGET_ALIASES.items():
        if collapsed == re.sub(r"[^a-z0-9]", "", alias):
            return canonical
    return raw


def classify_open_target(target: str) -> str:
    """Return 'url' or 'app' for an open/launch target name."""
    raw = canonical_open_target(target)
    if not raw:
        return "app"
    lower = raw.lower()
    if lower in _URL_FIRST_TARGETS or lower in KNOWN_SITES:
        return "url"
    if resolve_site_url(raw):
        return "url"
    if _looks_like_website(raw):
        return "url"
    return "app"


def resolve_site_url(target: str) -> Optional[str]:
    raw = canonical_open_target(target)
    if not raw:
        return None
    lower = raw.lower()
    if lower in KNOWN_SITES:
        return KNOWN_SITES[lower]
    original = (target or "").strip()
    if re.match(r"^https?://", original, re.IGNORECASE):
        return original
    if _looks_like_website(raw):
        cleaned = raw.strip().lstrip("/")
        if not cleaned.lower().startswith(("http://", "https://")):
            return f"https://{cleaned}"
    return None


def _looks_like_website(text: str) -> bool:
    raw = (text or "").strip()
    if not raw:
        return False
    if _RE_DOMAIN.match(raw):
        return True
    return bool(_RE_TLD.search(raw))


def is_valid_browser_destination(value: str) -> bool:
    """Reject internal/tool tokens masquerading as URLs or search queries."""
    cleaned = (value or "").strip().lower()
    if not cleaned:
        return False
    if cleaned in {"browser", "web", "internet", "none", "null", "true", "false"}:
        return False
    if cleaned.startswith("open_") or cleaned.endswith("_browser"):
        return False
    if re.fullmatch(r"[a-z0-9_]+", cleaned) and "_" in cleaned:
        if not resolve_site_url(cleaned.replace("_", " ")):
            return False
    return True


def resolve_browser_destination(raw: str) -> tuple[str, str]:
    """Return (url, spoken_line) for an open-browser request."""
    url = (raw or "").strip()
    if url and is_valid_browser_destination(url):
        if resolve_site_url(url):
            label = url
            return resolve_site_url(url) or url, f"Opening {label}."
        if url.startswith(("http://", "https://")):
            return url, f"Opening {url}."
    return "https://www.google.com", "Opening browser."


def build_search_url(query: str, *, download: bool = False) -> str:
    q = (query or "").strip()
    if download and "download" not in q.lower():
        q = f"{q} download"
    return "https://www.google.com/search?q=" + urllib.parse.quote_plus(q)


def open_in_browser(url: str) -> bool:
    try:
        return bool(webbrowser.open(url))
    except Exception:
        return False


def launch_app(name: str, *, launch: bool = True) -> tuple[bool, str]:
    """Launch a desktop app by friendly name. Returns (ok, spoken_line)."""
    display = canonical_open_target(name) or (name or "").strip() or "that"
    if sys.platform != "win32":
        return False, "I can only launch apps on Windows, sir."

    if display == "outlook":
        if not launch:
            return True, "Opening Outlook."
        return launch_outlook()

    office_exe = _OFFICE_APP_EXES.get(display.lower())
    if office_exe:
        if not launch:
            return True, f"Opening {display}."
        return launch_office_app(display, office_exe)

    path = find_app_path(display)
    if not path:
        if not launch:
            return False, f"I couldn't find {display} on this PC, sir."
        ok, spoken = open_url_or_site(display)
        if ok:
            return ok, spoken
        return False, f"I couldn't find {display} on this PC, sir."

    if not launch:
        return True, f"Opening {display}."

    if not _launch_gui_path(path):
        ok, spoken = open_url_or_site(display)
        if ok:
            return ok, spoken
        return False, f"I couldn't launch {display}, sir."
    return True, f"Opening {display}."


def open_document_with_default_app(path: Path) -> bool:
    """Open a file the same way as double-clicking it in Explorer."""
    if sys.platform != "win32":
        return False
    try:
        os.startfile(str(path))  # noqa: S606
        return True
    except OSError:
        return False


def launch_office_app(display_name: str, exe_name: str) -> tuple[bool, str]:
    """Launch Microsoft Office desktop apps via WINWORD.EXE-style paths."""
    if sys.platform != "win32":
        return False, "I can only launch apps on Windows, sir."

    if exe_name in _OFFICE_DOCUMENT_STARTERS:
        starter = _office_blank_starter(exe_name)
        if open_document_with_default_app(starter):
            return True, f"Opening {display_name}."

    exe_path = (
        _resolve_app_paths(exe_name)
        or _search_program_files(exe_name, display_name)
        or _where_exe(exe_name)
    )
    if exe_path and exe_name in _OFFICE_DOCUMENT_STARTERS:
        starter = _office_blank_starter(exe_name)
        try:
            if _shell_execute_app(exe_path, str(starter)):
                return True, f"Opening {display_name}."
        except OSError:
            pass

    candidates: list[str] = []
    for path in (
        exe_path,
        _best_start_menu_match(display_name),
    ):
        if path and path not in candidates:
            candidates.append(path)

    for path in candidates:
        if _launch_gui_path(path):
            return True, f"Opening {display_name}."

    return False, f"I couldn't launch {display_name}, sir."


_OFFICE_DOCUMENT_STARTERS: dict[str, tuple[str, str, str]] = {
    "WINWORD.EXE": ("new_document", ".rtf", "{\\rtf1\\ansi\\deff0\\par}"),
    "EXCEL.EXE": ("new_workbook", ".csv", ""),
    "POWERPNT.EXE": ("new_presentation", ".rtf", "{\\rtf1\\ansi\\deff0\\par}"),
}


def _office_output_dir() -> Path:
    path = Path.home() / "Documents" / "Jarvis"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _office_blank_starter(exe_name: str) -> Path:
    """Create a tiny starter file — some Office installs only open when given a document."""
    prefix, suffix, seed = _OFFICE_DOCUMENT_STARTERS.get(exe_name, ("new_file", ".txt", ""))
    path = _office_output_dir() / f"{prefix}_{time.strftime('%Y%m%d_%H%M%S')}{suffix}"
    path.write_text(seed, encoding="utf-8")
    return path


def _launch_gui_path(path: str) -> bool:
    """Launch a Windows GUI app or shortcut reliably from python/pythonw."""
    cleaned = (path or "").strip()
    if not cleaned:
        return False
    lower = cleaned.lower()
    if lower.endswith(".exe") and Path(cleaned).is_file():
        try:
            subprocess.Popen([cleaned])
            return True
        except OSError:
            pass
    return _run_path(cleaned)


def launch_outlook() -> tuple[bool, str]:
    """Try several Outlook install styles (New Outlook, Start Menu, classic)."""
    if sys.platform != "win32":
        return False, "I can only launch apps on Windows, sir."

    candidates: list[str] = []
    for path in (
        _new_outlook_path(),
        _best_start_menu_match("outlook"),
        _best_start_menu_match("outlook classic"),
        _resolve_app_paths("OUTLOOK.EXE"),
        _search_program_files("OUTLOOK.EXE", "outlook"),
    ):
        if path and path not in candidates:
            candidates.append(path)

    for path in candidates:
        if _run_path(path):
            return True, "Opening Outlook."

    if _shell_execute("ms-outlook:"):
        return True, "Opening Outlook."

    ok, spoken = open_url_or_site("outlook web")
    if ok:
        return True, "Opening Outlook on the web, sir."
    return False, "I couldn't open Outlook, sir."


def _shell_execute(path: str) -> bool:
    if sys.platform != "win32":
        return False
    import ctypes

    result = ctypes.windll.shell32.ShellExecuteW(None, "open", path, None, None, 1)
    return int(result) > 32


def _shell_execute_app(executable: str, parameters: str = "") -> bool:
    """Launch an executable with optional command-line parameters (Windows)."""
    if sys.platform != "win32":
        return False
    import ctypes

    result = ctypes.windll.shell32.ShellExecuteW(
        None,
        "open",
        executable,
        parameters or None,
        None,
        1,
    )
    return int(result) > 32


def _run_path(path: str) -> bool:
    """Launch a file/app without spawning a visible console window."""
    if _shell_execute(path):
        return True
    try:
        os.startfile(path)  # noqa: S606
        return True
    except OSError:
        return False


def open_url_or_site(target: str) -> tuple[bool, str]:
    url = resolve_site_url(target)
    if not url:
        return False, f"I don't know how to open {target.strip() or 'that'}, sir."
    if open_in_browser(url):
        label = target.strip() or "the site"
        return True, f"Opening {label}."
    return False, "I couldn't open the browser, sir."


def find_app_path(name: str) -> Optional[str]:
    raw = canonical_open_target(name) or (name or "").strip()
    if not raw:
        return None
    if sys.platform != "win32":
        return None

    lower = raw.lower()
    if lower in {"outlook", "microsoft outlook"} or (
        lower.startswith("outlook") and "classic" not in lower
    ):
        path = _resolve_outlook_path(prefer_classic=False)
        if path:
            return path
    if "outlook classic" in lower or lower == "outlook classic":
        path = _resolve_outlook_path(prefer_classic=True)
        if path:
            return path

    alias = APP_ALIASES.get(lower)
    if alias:
        resolved = _resolve_known_alias(alias, raw)
        if resolved:
            return resolved

    norm_query = normalize_label(raw)
    if not norm_query:
        return None

    best_path: Optional[str] = None
    best_score = 0
    for norm_name, path in _scan_start_menu():
        score = _match_score(norm_query, norm_name)
        if score > best_score:
            best_score = score
            best_path = path
    if best_path and best_score >= 0.55:
        return best_path

    for exe in _candidate_exe_names(raw):
        path = _resolve_app_paths(exe)
        if path:
            return path
        found = _where_exe(exe)
        if found:
            return found

    return None


def _resolve_known_alias(alias: str, display_name: str) -> Optional[str]:
    if alias.upper() == "OUTLOOK.EXE":
        return _resolve_outlook_path(prefer_classic="classic" in display_name.lower())

    # Prefer the real .exe from App Paths — Start Menu .lnk shortcuts often
    # report success from ShellExecute but fail to show a window from pythonw.
    if alias.upper() in _OFFICE_EXES:
        path = _resolve_app_paths(alias)
        if path:
            return path
        menu = _best_start_menu_match(display_name)
        if menu:
            return menu

    stem = normalize_label(Path(alias).stem)
    for norm_name, menu_path in _scan_start_menu():
        if stem and (stem in norm_name or norm_name.startswith(stem)):
            return menu_path

    if Path(alias).is_file():
        return alias
    path = _resolve_app_paths(alias)
    if path:
        return path
    found = _where_exe(alias)
    if found:
        return found
    return _search_program_files(Path(alias).name, display_name)


def _resolve_outlook_path(*, prefer_classic: bool) -> Optional[str]:
    if not prefer_classic:
        new_outlook = _new_outlook_path()
        if new_outlook:
            return new_outlook

    menu = _best_start_menu_match("outlook classic") or _best_start_menu_match("outlook")
    if menu:
        return menu
    path = _resolve_app_paths("OUTLOOK.EXE")
    if path:
        return path
    return _search_program_files("OUTLOOK.EXE", "outlook")


def _new_outlook_path() -> Optional[str]:
    alias = Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft/WindowsApps/olk.exe"
    if alias.exists():
        return str(alias)
    packaged_root = Path(os.environ.get("ProgramFiles", "")) / "WindowsApps"
    if not packaged_root.is_dir():
        return None
    best: Optional[str] = None
    try:
        for folder in packaged_root.glob("Microsoft.OutlookForWindows_*"):
            candidate = folder / "olk.exe"
            if candidate.is_file():
                best = str(candidate)
    except OSError:
        return best
    return best


def _best_start_menu_match(name: str) -> Optional[str]:
    norm_query = normalize_label(name)
    if not norm_query:
        return None
    best_path: Optional[str] = None
    best_score = 0.0
    for norm_name, path in _scan_start_menu():
        score = _match_score(norm_query, norm_name)
        if score > best_score:
            best_score = score
            best_path = path
    if best_path and best_score >= 0.55:
        return best_path
    return None


def _candidate_exe_names(name: str) -> list[str]:
    cleaned = re.sub(r"[^a-zA-Z0-9 ]", " ", name).strip()
    parts = [p for p in cleaned.split() if p]
    candidates: list[str] = []
    if parts:
        candidates.append("".join(parts) + ".exe")
        candidates.append(parts[0] + ".exe")
        candidates.append(" ".join(parts) + ".exe")
    return candidates


def _match_score(query: str, candidate: str) -> float:
    if not query or not candidate:
        return 0.0
    if query == candidate:
        return 1.0
    if candidate.startswith(query) or query.startswith(candidate):
        return 0.85
    if query in candidate or candidate in query:
        return max(len(query), len(candidate)) / max(len(query), len(candidate), 1) * 0.8
    return 0.0


@lru_cache(maxsize=1)
def _scan_start_menu() -> tuple[tuple[str, str], ...]:
    if sys.platform != "win32":
        return ()
    roots = [
        Path(os.environ.get("ProgramData", "")) / "Microsoft/Windows/Start Menu/Programs",
        Path(os.environ.get("APPDATA", "")) / "Microsoft/Windows/Start Menu/Programs",
    ]
    found: list[tuple[str, str]] = []
    seen: set[str] = set()
    for root in roots:
        if not root.is_dir():
            continue
        for path in root.rglob("*.lnk"):
            key = str(path).lower()
            if key in seen:
                continue
            seen.add(key)
            found.append((normalize_label(path.stem), str(path)))
    return tuple(found)


def _resolve_app_paths(exe_name: str) -> Optional[str]:
    if sys.platform != "win32":
        return None
    for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        try:
            with winreg.OpenKey(
                hive,
                rf"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\{exe_name}",
            ) as key:
                path, _ = winreg.QueryValueEx(key, None)
                if path and Path(str(path)).exists():
                    return str(path)
        except OSError:
            continue
    return None


def _where_exe(name: str) -> Optional[str]:
    try:
        flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        out = subprocess.run(
            ["where", name],
            capture_output=True,
            text=True,
            timeout=5,
            creationflags=flags,
            check=False,
        )
        if out.returncode != 0:
            return None
        for line in out.stdout.strip().splitlines():
            candidate = line.strip()
            if candidate and Path(candidate).exists():
                return candidate
    except (OSError, subprocess.SubprocessError):
        return None
    return None


def _search_program_files(exe_name: str, hint: str) -> Optional[str]:
    roots = [
        Path(os.environ.get("ProgramFiles", r"C:\Program Files")),
        Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")),
        Path(os.environ.get("LOCALAPPDATA", "")),
    ]
    exe_lower = exe_name.lower()
    hint_norm = normalize_label(hint)
    best: Optional[str] = None
    best_depth = 999
    for root in roots:
        if not root.is_dir():
            continue
        try:
            for path in root.rglob(exe_name):
                if not path.is_file():
                    continue
                depth = len(path.parts)
                if hint_norm and hint_norm in normalize_label(str(path.parent)):
                    return str(path)
                if depth < best_depth:
                    best = str(path)
                    best_depth = depth
        except OSError:
            continue
    return best
