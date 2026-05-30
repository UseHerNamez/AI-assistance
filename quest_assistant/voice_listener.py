from __future__ import annotations

import json
import os
import queue
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

import numpy as np
import sounddevice as sd
from vosk import KaldiRecognizer, Model

from quest_assistant.diagnostics import log_diagnostic


DEFAULT_MODEL_DIR = Path.home() / ".quest_assistant" / "models" / "vosk-model-small-en-us-0.15"
_AUDIO_QUEUE_MAX = 32
_THREAD_JOIN_S = 3.0

_MODEL_CACHE: Optional[Model] = None
_MODEL_CACHE_LOCK = threading.Lock()


def _get_vosk_model(model_path: Path) -> Model:
    global _MODEL_CACHE
    with _MODEL_CACHE_LOCK:
        if _MODEL_CACHE is None:
            _MODEL_CACHE = Model(str(model_path))
        return _MODEL_CACHE


def resolve_vosk_model_path() -> Optional[Path]:
    env = os.environ.get("VOSK_MODEL_PATH")
    if env:
        p = Path(env).expanduser()
        if p.exists():
            return p
    if DEFAULT_MODEL_DIR.exists():
        return DEFAULT_MODEL_DIR
    return None


@dataclass(frozen=True)
class VoiceConfig:
    sample_rate: int = 16000
    device: Optional[int] = None


class WakeWordGate:
    """
    Filters recognized speech so commands only flow after the wake word.

    Behavior:
    - If an utterance contains the wake word (default "jarvis"), we "arm" a short window.
    - Any remaining text in that same utterance (after removing the wake word) is emitted as a command.
    - While armed, subsequent utterances without the wake word are also emitted as commands.
    - Outside the armed window, utterances without the wake word are ignored.
    """

    def __init__(self, wake_word: str = "jarvis", command_window_s: float = 8.0) -> None:
        self.wake_word = wake_word.strip().lower()
        self.command_window_s = float(command_window_s)
        self._armed_until = 0.0

    def disarm(self) -> None:
        self._armed_until = 0.0

    def _strip_wake_word(self, raw: str) -> Optional[str]:
        if not self.wake_word:
            return raw.strip() or None
        lower = raw.lower()
        if self.wake_word in lower.split():
            wake_present = True
        else:
            tokens = [t.strip(".,!?;:") for t in lower.split()]
            wake_present = self.wake_word in tokens
        if not wake_present:
            return raw.strip() or None
        parts = [p for p in raw.split() if p.strip(".,!?;:").lower() != self.wake_word]
        remainder = " ".join(parts).strip(" ,.!?;:\t")
        return remainder or None

    def feed(self, text: str, *, require_wake_word: bool = True) -> Optional[str]:
        raw = (text or "").strip()
        if not raw:
            return None

        if not require_wake_word:
            return self._strip_wake_word(raw) or raw

        if not self.wake_word:
            return raw

        now = time.monotonic()
        lower = raw.lower()

        wake_present = False
        if self.wake_word in lower.split():
            wake_present = True
        else:
            tokens = [t.strip(".,!?;:") for t in lower.split()]
            wake_present = self.wake_word in tokens

        if wake_present:
            self._armed_until = now + self.command_window_s
            remainder = self._strip_wake_word(raw)
            return remainder or None

        if now <= self._armed_until:
            return raw

        return None


class VoiceListener:
    """
    Always-listening local speech-to-text.

    Emits *final* recognized utterances via on_text callback.
    """

    def __init__(
        self,
        on_text: Callable[[str], None],
        config: VoiceConfig | None = None,
        *,
        wake_word: str = "jarvis",
        command_window_s: float = 8.0,
        on_error: Optional[Callable[[str], None]] = None,
    ):
        self.on_text = on_text
        self.on_error = on_error
        self.config = config or VoiceConfig()
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._running = threading.Event()
        self._gate = WakeWordGate(wake_word=wake_word, command_window_s=command_window_s)
        self._require_wake_word = True
        self._gate_lock = threading.Lock()
        self._rec: Optional[KaldiRecognizer] = None
        self._results_muted = False
        self._generation = 0
        self.last_error: Optional[str] = None

    def set_wake_word_required(self, required: bool) -> None:
        with self._gate_lock:
            self._require_wake_word = required
            if required:
                self._gate.disarm()

    def set_results_muted(self, muted: bool) -> None:
        with self._gate_lock:
            self._results_muted = muted
        if not muted:
            rec = self._rec
            if rec is not None:
                try:
                    rec.Reset()
                except Exception as exc:
                    log_diagnostic("voice", "recognizer reset failed", exc=exc)

    @property
    def is_running(self) -> bool:
        return self._running.is_set()

    @property
    def is_starting(self) -> bool:
        thread = self._thread
        return thread is not None and thread.is_alive() and not self._running.is_set()

    def _report_error(self, message: str, *, exc: BaseException | None = None) -> None:
        self.last_error = message
        log_diagnostic("voice", message, exc=exc)
        if self.on_error:
            try:
                self.on_error(message)
            except Exception as cb_exc:
                log_diagnostic("voice", "on_error callback failed", exc=cb_exc)

    def _clear_error(self) -> None:
        self.last_error = None

    def start(self) -> None:
        thread = self._thread
        if thread is not None and thread.is_alive():
            if not self._stop.is_set():
                return
            self._stop.set()
            thread.join(timeout=_THREAD_JOIN_S)
            if thread.is_alive():
                # Abandon the stuck thread; a new generation tells it to exit quickly.
                self._generation += 1
                log_diagnostic("voice", "prior listener thread did not stop; starting a new one")
        self._thread = None
        self._stop.clear()
        self._generation += 1
        generation = self._generation
        self._thread = threading.Thread(
            target=self._run,
            args=(generation,),
            name="VoiceListener",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._generation += 1
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=_THREAD_JOIN_S)
        self._thread = None
        self._running.clear()
        self._rec = None

    def restart(self) -> None:
        if self.is_starting:
            return
        self.stop()
        self._stop.clear()
        self.start()

    def _run(self, generation: int) -> None:
        if generation != self._generation:
            return

        model_path = resolve_vosk_model_path()
        if not model_path:
            self._report_error(
                "Speech model not found. Run download_vosk_model.ps1 or set VOSK_MODEL_PATH."
            )
            return

        try:
            model = _get_vosk_model(model_path)
        except Exception as exc:
            self._report_error("Failed to load the speech model.", exc=exc)
            return

        if generation != self._generation or self._stop.is_set():
            return

        rec = KaldiRecognizer(model, self.config.sample_rate)
        rec.SetWords(False)
        self._rec = rec

        audio_q: "queue.Queue[np.ndarray]" = queue.Queue(maxsize=_AUDIO_QUEUE_MAX)

        def callback(indata, frames, time_info, status) -> None:  # noqa: ANN001
            if self._stop.is_set() or generation != self._generation:
                return
            if status:
                log_diagnostic("voice", f"audio input status: {status}")
            chunk = indata.copy().reshape(-1)
            try:
                audio_q.put_nowait(chunk)
            except queue.Full:
                try:
                    audio_q.get_nowait()
                    audio_q.put_nowait(chunk)
                except queue.Empty:
                    pass

        try:
            stream = sd.InputStream(
                samplerate=self.config.sample_rate,
                channels=1,
                dtype="int16",
                callback=callback,
                device=self.config.device,
                blocksize=8000,
            )
        except Exception as exc:
            self._rec = None
            self._report_error("Microphone unavailable. Check Windows mic permissions.", exc=exc)
            return

        self._clear_error()
        self._running.set()
        try:
            with stream:
                while not self._stop.is_set() and generation == self._generation:
                    try:
                        data = audio_q.get(timeout=0.2)
                    except queue.Empty:
                        continue

                    try:
                        if rec.AcceptWaveform(data.tobytes()):
                            result = json.loads(rec.Result() or "{}")
                            text = (result.get("text") or "").strip()
                            if not text:
                                continue
                            with self._gate_lock:
                                require_wake = self._require_wake_word
                            cmd = self._gate.feed(text, require_wake_word=require_wake)
                            if cmd:
                                with self._gate_lock:
                                    if self._results_muted:
                                        continue
                                self.on_text(cmd)
                    except Exception as exc:
                        log_diagnostic("voice", "recognition step failed", exc=exc)
                        continue
        finally:
            self._rec = None
            self._running.clear()
