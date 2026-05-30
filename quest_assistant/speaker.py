from __future__ import annotations

import asyncio
import hashlib
import os
import queue
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

try:
    import edge_tts
except Exception:  # pragma: no cover - optional dependency
    edge_tts = None

from quest_assistant.diagnostics import log_diagnostic

# Local Windows voice by default — Edge TTS is online (set JARVIS_TTS_BACKEND=edge to opt in).
_DEFAULT_TTS_BACKEND = "sapi"
_MAX_ROUTINE_QUEUE = 4
_MALE_SAPI_VOICE: str | None = None
_MALE_SAPI_CHECKED = False
_EDGE_FALLBACK_LOGGED = False
_MALE_SAPI_NAME_TERMS = (
    "david",
    "mark",
    "george",
    "guy",
    "james",
    "richard",
    "ryan",
    "christopher",
    "male",
)


def _tts_backend() -> str:
    backend = os.environ.get("JARVIS_TTS_BACKEND", _DEFAULT_TTS_BACKEND).strip().lower()
    if backend not in {"edge", "auto", "sapi"}:
        return _DEFAULT_TTS_BACKEND
    return backend


def _edge_voice_name() -> str:
    return os.environ.get("JARVIS_EDGE_VOICE", "en-GB-RyanNeural").strip() or "en-GB-RyanNeural"


def _resolve_male_sapi_voice() -> str | None:
    """Return an installed English male SAPI voice name, if any."""
    global _MALE_SAPI_VOICE, _MALE_SAPI_CHECKED
    if _MALE_SAPI_CHECKED:
        return _MALE_SAPI_VOICE

    _MALE_SAPI_CHECKED = True
    explicit = os.environ.get("JARVIS_SAPI_VOICE", "").strip()
    if explicit:
        _MALE_SAPI_VOICE = explicit
        return explicit

    terms = "|".join(_MALE_SAPI_NAME_TERMS)
    script = (
        "Add-Type -AssemblyName System.Speech; "
        "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
        "$terms = @('" + "','".join(_MALE_SAPI_NAME_TERMS) + "'); "
        "$voices = @($s.GetInstalledVoices() | ForEach-Object { $_.VoiceInfo } | "
        "Where-Object { $_.Gender -eq 'Male' -and $_.Culture.Name -like 'en*' }); "
        "foreach ($term in $terms) { "
        "  $match = $voices | Where-Object { "
        "    $_.Name -match $term -or $_.Description -match $term "
        "  } | Select-Object -First 1; "
        "  if ($match) { Write-Output $match.Name; exit 0 } "
        "}; "
        "if ($voices.Count -gt 0) { Write-Output $voices[0].Name; exit 0 }; "
        "exit 1"
    )
    try:
        completed = subprocess.run(
            _powershell_args("-Command", script),
            capture_output=True,
            text=True,
            timeout=8,
            check=False,
            **_hidden_subprocess_kwargs(),
        )
        if completed.returncode == 0:
            name = (completed.stdout or "").strip().splitlines()[0].strip()
            if name:
                _MALE_SAPI_VOICE = name
                return name
    except Exception:
        pass

    _MALE_SAPI_VOICE = None
    return None


def _log_edge_fallback_once(reason: str) -> None:
    global _EDGE_FALLBACK_LOGGED
    if _EDGE_FALLBACK_LOGGED:
        return
    _EDGE_FALLBACK_LOGGED = True
    log_diagnostic(
        "tts",
        f"{reason} Using Edge neural voice {_edge_voice_name()}. "
        "Install a male Windows speech voice (Settings → Time & language → Speech) "
        "or set JARVIS_TTS_BACKEND=edge to keep this voice.",
    )


def _powershell_args(*parts: str) -> list[str]:
    return ["powershell", "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden", *parts]


def _hidden_subprocess_kwargs() -> dict:
    if os.name != "nt":
        return {}
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startupinfo.wShowWindow = subprocess.SW_HIDE
    return {
        "startupinfo": startupinfo,
        "creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000),
    }


def _ps_single_quote(text: str) -> str:
    return (text or "").replace("'", "''")


@dataclass(frozen=True)
class _Utterance:
    text: str
    on_done: Optional[Callable[[], None]] = None


class Speaker:
    """Small async wrapper around Jarvis speech output."""

    def __init__(self, on_speak_state: Optional[Callable[[bool], None]] = None) -> None:
        self._on_speak_state = on_speak_state
        self._queue: "queue.Queue[Optional[_Utterance]]" = queue.Queue()
        self._cache_dir = Path.home() / ".quest_assistant" / "tts_cache"
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        self._player_proc: Optional[subprocess.Popen] = None
        self._player_lock = threading.Lock()
        self._thread = threading.Thread(target=self._run, daemon=True, name="Speaker")
        self._thread.start()

    def say(self, text: str, on_done: Optional[Callable[[], None]] = None) -> None:
        text = (text or "").strip()
        if not text:
            return
        if on_done is None:
            self._trim_routine_queue()
        self._queue.put(_Utterance(text, on_done))

    def preload(self, texts: list[str]) -> None:
        if _tts_backend() != "edge" or edge_tts is None:
            return
        threading.Thread(target=self._preload_edge_cache, args=(texts,), daemon=True).start()

    def stop(self) -> None:
        self._queue.put(None)

    def shutdown(self, timeout_s: float = 6.0) -> None:
        """Stop the worker and wait for playback to finish."""
        self._queue.put(None)
        self._thread.join(timeout=timeout_s)

    def _trim_routine_queue(self) -> None:
        # Keep a few confirmations — do not drop everything except the latest line.
        pending: list[_Utterance] = []
        while True:
            try:
                item = self._queue.get_nowait()
            except queue.Empty:
                break
            if item is None:
                self._queue.put(None)
                return
            pending.append(item)
        drop = max(0, len(pending) - _MAX_ROUTINE_QUEUE)
        for utterance in pending[drop:]:
            self._queue.put(utterance)

    def _preload_edge_cache(self, texts: list[str]) -> None:
        voice = _edge_voice_name()
        for text in texts:
            text = (text or "").strip()
            if not text:
                continue
            try:
                path = self._cached_edge_path(text, voice)
                if path.exists():
                    continue
                tmp_path = path.with_suffix(".tmp.mp3")
                asyncio.run(edge_tts.Communicate(text=text, voice=voice).save(str(tmp_path)))
                tmp_path.replace(path)
            except Exception:
                continue

    def _run(self) -> None:
        backend = _tts_backend()
        edge_voice = _edge_voice_name()
        male_sapi = _resolve_male_sapi_voice()
        use_edge_only = backend == "edge" and edge_tts is not None
        use_edge_if_no_male = backend == "sapi" and male_sapi is None and edge_tts is not None
        if use_edge_if_no_male:
            _log_edge_fallback_once("No male local Windows voice found (only female SAPI voices installed).")

        use_edge = (use_edge_only or use_edge_if_no_male) and edge_tts is not None
        if use_edge:
            self._ensure_player_proc()

        while True:
            item = self._queue.get()
            if item is None:
                self._shutdown_player_proc()
                break

            utterance = item
            text = utterance.text
            on_done = utterance.on_done

            self._notify_speak_state(True)
            try:
                spoken = False
                if use_edge:
                    spoken = self._say_with_edge(text, edge_voice)
                elif backend == "auto":
                    spoken = self._say_with_sapi(text, male_sapi)
                    if not spoken and edge_tts is not None:
                        _log_edge_fallback_once("Local male voice unavailable; falling back to Edge TTS.")
                        spoken = self._say_with_edge(text, edge_voice)
                else:
                    spoken = self._say_with_sapi(text, male_sapi)
            finally:
                self._notify_speak_state(False)
                if on_done is not None:
                    self._invoke_done(on_done)

    def _invoke_done(self, callback: Callable[[], None]) -> None:
        try:
            callback()
        except Exception:
            pass

    def _notify_speak_state(self, speaking: bool) -> None:
        if not self._on_speak_state:
            return
        try:
            self._on_speak_state(speaking)
        except Exception:
            pass

    def _say_with_sapi(self, text: str, voice_name: str | None) -> bool:
        if not voice_name:
            return False
        if self._say_with_sapi_powershell(text, voice_name):
            return True
        try:
            import pyttsx3

            engine = pyttsx3.init()
            engine.setProperty("rate", 175)
            engine.setProperty("volume", 0.95)
            Speaker._prefer_male_voice(engine)
            engine.say(text)
            engine.runAndWait()
            try:
                engine.stop()
            except Exception:
                pass
            return True
        except Exception:
            return False

    @staticmethod
    def _say_with_sapi_powershell(text: str, voice_name: str) -> bool:
        quoted_text = _ps_single_quote(text)
        quoted_voice = _ps_single_quote(voice_name)
        script = (
            "Add-Type -AssemblyName System.Speech; "
            "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
            "$s.Rate = 1; "
            f"$s.SelectVoice('{quoted_voice}'); "
            f"$s.Speak('{quoted_text}');"
        )
        try:
            completed = subprocess.run(
                _powershell_args("-Command", script),
                capture_output=True,
                text=True,
                timeout=max(8, min(45, len(text.split()) + 6)),
                check=False,
                **_hidden_subprocess_kwargs(),
            )
            return completed.returncode == 0
        except Exception:
            return False

    def _say_with_edge(self, text: str, voice: str) -> bool:
        try:
            path = self._cached_edge_path(text, voice)
            if not path.exists():
                tmp_path = path.with_suffix(".tmp.mp3")
                asyncio.run(edge_tts.Communicate(text=text, voice=voice).save(str(tmp_path)))
                tmp_path.replace(path)
            return self._play_mp3(path, wait_s=_estimate_speech_seconds(text))
        except Exception:
            return False

    def _cached_edge_path(self, text: str, voice: str) -> Path:
        digest = hashlib.sha1(f"{voice}\n{text}".encode("utf-8")).hexdigest()
        return self._cache_dir / f"{digest}.mp3"

    def _play_mp3(self, path: Path, *, wait_s: float) -> bool:
        if self._play_mp3_with_powershell(path, wait_s=wait_s):
            return True
        return self._play_mp3_with_wmp(path)

    def _ensure_player_proc(self) -> Optional[subprocess.Popen]:
        proc = self._player_proc
        if proc is not None and proc.poll() is None:
            return proc
        try:
            proc = subprocess.Popen(
                _powershell_args("-Command", "-"),
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                text=True,
                bufsize=1,
                **_hidden_subprocess_kwargs(),
            )
            assert proc.stdin is not None
            proc.stdin.write(
                "Add-Type -AssemblyName PresentationCore; "
                "$m = New-Object System.Windows.Media.MediaPlayer;\n"
            )
            proc.stdin.flush()
            self._player_proc = proc
            return proc
        except Exception:
            self._player_proc = None
            return None

    def _shutdown_player_proc(self) -> None:
        proc = self._player_proc
        self._player_proc = None
        if proc is None:
            return
        try:
            if proc.stdin is not None:
                proc.stdin.write("exit\n")
                proc.stdin.flush()
                proc.stdin.close()
        except Exception:
            pass
        try:
            proc.wait(timeout=2)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass

    def _play_mp3_with_wmp(self, path: Path) -> bool:
        pythoncom = None
        player = None
        try:
            import pythoncom
            import win32com.client

            pythoncom.CoInitialize()
            player = win32com.client.Dispatch("WMPlayer.OCX")
            player.URL = str(path)
            player.controls.play()

            start_deadline = time.monotonic() + 0.8
            while time.monotonic() < start_deadline:
                if player.playState == 3:
                    break
                time.sleep(0.05)
            else:
                return False

            end_deadline = time.monotonic() + 30.0
            while time.monotonic() < end_deadline:
                if player.playState in (1, 8):
                    return True
                time.sleep(0.05)
            return False
        except Exception:
            return False
        finally:
            try:
                if player is not None:
                    player.close()
            except Exception:
                pass
            if pythoncom is not None:
                try:
                    pythoncom.CoUninitialize()
                except Exception:
                    pass

    @staticmethod
    def _play_mp3_with_powershell(path: Path, *, wait_s: float) -> bool:
        try:
            uri = path.as_uri().replace("'", "''")
            play_budget_s = min(18.0, max(1.5, wait_s) + 0.5)
            script = (
                "Add-Type -AssemblyName PresentationCore; "
                "$m = New-Object System.Windows.Media.MediaPlayer; "
                f"$m.Open([Uri]'{uri}'); "
                "$deadline = (Get-Date).AddMilliseconds(500); "
                "while (-not $m.NaturalDuration.HasTimeSpan -and (Get-Date) -lt $deadline) "
                "{ Start-Sleep -Milliseconds 10 }; "
                "$m.Play(); "
                f"$end = (Get-Date).AddSeconds({play_budget_s}); "
                "while ((Get-Date) -lt $end) { "
                "  if ($m.NaturalDuration.HasTimeSpan -and "
                "      $m.Position.TotalSeconds -ge ($m.NaturalDuration.TimeSpan.TotalSeconds - 0.05)) { break }; "
                "  Start-Sleep -Milliseconds 40 "
                "}; "
                "$m.Close();"
            )
            completed = subprocess.run(
                _powershell_args("-Command", script),
                capture_output=True,
                text=True,
                timeout=max(12, int(play_budget_s) + 6),
                check=False,
                **_hidden_subprocess_kwargs(),
            )
            return completed.returncode == 0
        except Exception:
            return False

    @staticmethod
    def _prefer_male_voice(engine) -> None:  # noqa: ANN001
        voices = engine.getProperty("voices") or []
        if not voices:
            return

        preferred_terms = (
            "david",
            "mark",
            "george",
            "richard",
            "james",
            "guy",
            "ryan",
            "male",
            "en-gb",
            "en-us",
        )
        fallback = voices[0]

        for voice in voices:
            haystack = " ".join(
                str(getattr(voice, attr, "") or "") for attr in ("id", "name", "gender")
            ).lower()
            if any(term in haystack for term in preferred_terms):
                engine.setProperty("voice", voice.id)
                return

        engine.setProperty("voice", fallback.id)


def _estimate_speech_seconds(text: str) -> float:
    words = max(1, len((text or "").split()))
    return min(12.0, max(1.6, 0.85 + (words * 0.34)))
