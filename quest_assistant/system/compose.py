from __future__ import annotations

import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

from quest_assistant.system.launcher import _run_path, find_app_path


def compose_output_dir() -> Path:
    path = Path.home() / "Documents" / "Jarvis"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _slugify(text: str, *, max_len: int = 42) -> str:
    slug = re.sub(r"[^\w\s-]", "", (text or "").lower())
    slug = re.sub(r"\s+", "_", slug.strip())
    return (slug[:max_len] or "draft").strip("_")


def write_draft_file(content: str, topic: str, *, suffix: str) -> Path:
    stamp = time.strftime("%Y%m%d_%H%M%S")
    path = compose_output_dir() / f"{_slugify(topic)}_{stamp}.{suffix}"
    path.write_text(content, encoding="utf-8")
    return path


def open_in_notepad(content: str, topic: str) -> tuple[bool, str]:
    path = write_draft_file(content, topic, suffix="txt")
    try:
        subprocess.Popen(
            ["notepad.exe", str(path)],
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
        return True, "I wrote your draft in Notepad, sir."
    except OSError:
        return False, "I couldn't open Notepad, sir."


def open_in_word(content: str, topic: str) -> tuple[bool, str]:
    path = write_draft_file(content, topic, suffix="txt")
    word_path = find_app_path("word")
    if word_path:
        try:
            subprocess.Popen(
                [word_path, str(path)],
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
            )
            return True, "I opened your draft in Word, sir."
        except OSError:
            pass
    if _run_path(str(path)):
        return True, "I opened your draft, sir."
    return False, "I couldn't open Word, sir."


def _powershell_escape(value: str) -> str:
    return (value or "").replace("'", "''")


def open_in_outlook_draft(content: str, topic: str) -> tuple[bool, str]:
    if sys.platform != "win32":
        return False, "Outlook drafts are only supported on Windows, sir."

    body_path = write_draft_file(content, topic, suffix="txt")
    subject = topic.strip().rstrip(".")
    if subject and not subject[0].isupper():
        subject = subject[0].upper() + subject[1:]

    script = (
        "$bodyPath = '" + _powershell_escape(str(body_path)) + "'; "
        "$subject = '" + _powershell_escape(subject) + "'; "
        "$body = Get-Content -LiteralPath $bodyPath -Raw -Encoding UTF8; "
        "$ol = New-Object -ComObject Outlook.Application; "
        "$mail = $ol.CreateItem(0); "
        "$mail.Subject = $subject; "
        "$mail.Body = $body; "
        "$mail.Display() | Out-Null"
    )
    try:
        completed = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True,
            text=True,
            timeout=30,
            creationflags=subprocess.CREATE_NO_WINDOW,
            check=False,
        )
        if completed.returncode == 0:
            return True, "I opened an Outlook draft for you, sir."
    except (OSError, subprocess.SubprocessError):
        pass

    # Fallback: open Outlook and leave the draft file in Documents/Jarvis.
    from quest_assistant.system.launcher import launch_outlook

    ok, _ = launch_outlook()
    if ok:
        return True, (
            "I saved the draft text and opened Outlook, sir. "
            f"You can copy it from {body_path.name} in Documents\\Jarvis."
        )
    return False, "I couldn't open an Outlook draft, sir."


def deliver_compose(destination: str, content: str, topic: str) -> tuple[bool, str]:
    dest = (destination or "notepad").strip().lower()
    if dest == "outlook":
        return open_in_outlook_draft(content, topic)
    if dest == "word":
        return open_in_word(content, topic)
    return open_in_notepad(content, topic)
