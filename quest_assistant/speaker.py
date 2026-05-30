from __future__ import annotations

import asyncio
import hashlib
import os
import queue
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import Optional

import pyttsx3

try:
    import edge_tts
except Exception:  # pragma: no cover - optional dependency
    edge_tts = None


class Speaker:
    """Small async wrapper around Jarvis speech output."""

    def __init__(self) -> None:
        self._queue: "queue.Queue[Optional[str]]" = queue.Queue()
        self._cache_dir = Path.home() / ".quest_assistant" / "tts_cache"
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        self._player_proc: Optional[subprocess.Popen] = None
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def say(self, text: str) -> None:
        text = (text or "").strip()
        if text:
            self._queue.put(text)

    def preload(self, texts: list[str]) -> None:
        threading.Thread(target=self._preload_edge_cache, args=(texts,), daemon=True).start()

    def stop(self) -> None:
        self._queue.put(None)

    def _preload_edge_cache(self, texts: list[str]) -> None:
        backend = os.environ.get("JARVIS_TTS_BACKEND", "auto").strip().lower()
        if backend == "sapi" or edge_tts is None:
            return
        voice = os.environ.get("JARVIS_EDGE_VOICE", "en-GB-RyanNeural").strip()
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
        backend = os.environ.get("JARVIS_TTS_BACKEND", "auto").strip().lower()
        edge_voice = os.environ.get("JARVIS_EDGE_VOICE", "en-GB-RyanNeural").strip()

        edge_available = backend != "sapi" and edge_tts is not None
        sapi_engine = self._init_sapi()
        if edge_available:
            # Pre-spawn the persistent media host so the first reply is not slowed
            # by the one-time PowerShell startup cost.
            self._ensure_player_proc()

        while True:
            text = self._queue.get()
            if text is None:
                self._shutdown_player_proc()
                break
            text = self._coalesce_pending_text(text)

            spoken = False
            if backend == "auto" and sapi_engine and _is_short_confirmation(text):
                self._say_with_sapi(sapi_engine, text)
                spoken = True

            if not spoken and edge_available:
                spoken = self._say_with_edge(text, edge_voice)

            if not spoken and sapi_engine:
                self._say_with_sapi(sapi_engine, text)

    def _init_sapi(self):  # noqa: ANN201
        try:
            engine = pyttsx3.init()
            engine.setProperty("rate", 175)
            engine.setProperty("volume", 0.95)
            self._prefer_male_voice(engine)
            return engine
        except Exception:
            return None

    def _say_with_edge(self, text: str, voice: str) -> bool:
        path: Optional[Path] = None
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
        # A persistent PowerShell media host avoids paying the ~0.6s process
        # spawn cost on every utterance, so speech starts almost immediately.
        if self._play_mp3_persistent(path, wait_s=wait_s):
            return True
        # PresentationCore (PowerShell) one-shot is the reliable fallback on
        # Windows; WMP is only a fast-failing last resort because it can block
        # for seconds before reporting it never actually started playback.
        return self._play_mp3_with_powershell(path, wait_s=wait_s) or self._play_mp3_with_wmp(path)

    def _ensure_player_proc(self) -> Optional[subprocess.Popen]:
        proc = self._player_proc
        if proc is not None and proc.poll() is None:
            return proc
        try:
            proc = subprocess.Popen(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command", "-"],
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                text=True,
                bufsize=1,
            )
            assert proc.stdin is not None
            # Load the audio framework and create a single reusable player once.
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

    def _play_mp3_persistent(self, path: Path, *, wait_s: float) -> bool:
        proc = self._ensure_player_proc()
        if proc is None or proc.stdin is None:
            return False
        try:
            uri = path.as_uri().replace("'", "''")
            # Re-open the reusable player on the persistent host, wait briefly
            # for the local file to buffer, then start playback immediately.
            proc.stdin.write(
                f"$m.Stop(); $m.Open([Uri]'{uri}'); "
                "$d = (Get-Date).AddMilliseconds(450); "
                "while (-not $m.NaturalDuration.HasTimeSpan -and (Get-Date) -lt $d) "
                "{ Start-Sleep -Milliseconds 8 }; "
                "$m.Play();\n"
            )
            proc.stdin.flush()
        except Exception:
            self._player_proc = None
            return False
        # Playback runs in the persistent host; wait here for it to finish so the
        # next queued phrase does not cut this one off.
        time.sleep(max(0.4, wait_s))
        return proc.poll() is None

    def _play_mp3_with_wmp(self, path: Path) -> bool:
        pythoncom = None
        try:
            import pythoncom
            import win32com.client

            pythoncom.CoInitialize()
            player = win32com.client.Dispatch("WMPlayer.OCX")
            player.URL = str(path)
            player.controls.play()

            started = False
            start_deadline = time.monotonic() + 0.8
            while time.monotonic() < start_deadline:
                if player.playState == 3:  # playing
                    started = True
                    break
                time.sleep(0.05)

            if not started:
                return False

            end_deadline = time.monotonic() + 30.0
            while time.monotonic() < end_deadline:
                if player.playState in (1, 8):  # stopped, media ended
                    return True
                time.sleep(0.05)
            return False
        except Exception:
            return False
        finally:
            try:
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
        # Windows MediaPlayer COM can report "ready" without actually playing.
        # PresentationCore MediaPlayer is a reliable silent fallback for MP3.
        try:
            uri = path.as_uri().replace("'", "''")
            wait_ms = int(max(1300, min(12000, wait_s * 1000)))
            # Open() buffers asynchronously; poll NaturalDuration (capped) instead
            # of a fixed long sleep so playback starts as soon as the file is ready.
            script = (
                "Add-Type -AssemblyName PresentationCore; "
                "$m = New-Object System.Windows.Media.MediaPlayer; "
                f"$m.Open([Uri]'{uri}'); "
                "$deadline = (Get-Date).AddMilliseconds(500); "
                "while (-not $m.NaturalDuration.HasTimeSpan -and (Get-Date) -lt $deadline) "
                "{ Start-Sleep -Milliseconds 10 }; "
                "$m.Play(); "
                f"Start-Sleep -Milliseconds {wait_ms}; "
                "$m.Close();"
            )
            completed = subprocess.run(
                ["powershell", "-NoProfile", "-Command", script],
                capture_output=True,
                text=True,
                timeout=max(10, int(wait_s) + 4),
                check=False,
            )
            return completed.returncode == 0
        except Exception:
            return False

    def _coalesce_pending_text(self, first_text: str) -> str:
        # Several confirmations can be queued in quick succession. Speaking them
        # as one sentence avoids paying Edge playback startup cost for each one.
        parts = [first_text]
        time.sleep(0.05)
        while True:
            try:
                next_text = self._queue.get_nowait()
            except queue.Empty:
                break
            if next_text is None:
                self._queue.put(None)
                break
            parts.append(next_text)
        return " ".join(parts)

    @staticmethod
    def _say_with_sapi(engine, text: str) -> None:  # noqa: ANN001
        try:
            engine.say(text)
            engine.runAndWait()
        except Exception:
            return

    @staticmethod
    def _prefer_male_voice(engine) -> None:  # noqa: ANN001
        voices = engine.getProperty("voices") or []
        if not voices:
            return

        preferred_terms = ("david", "mark", "george", "richard", "male")
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


def _is_short_confirmation(text: str) -> bool:
    words = len((text or "").split())
    return words <= 14
