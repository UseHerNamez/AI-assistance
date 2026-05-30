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


DEFAULT_MODEL_DIR = Path.home() / ".quest_assistant" / "models" / "vosk-model-small-en-us-0.15"


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

        # If wake word is present as a word boundary, arm and strip it.
        # We allow forms like: "jarvis, ..." or "hey jarvis ..."
        wake_present = False
        if self.wake_word in lower.split():
            wake_present = True
        else:
            # Fallback: handle punctuation glued to the wake word.
            tokens = [t.strip(".,!?;:") for t in lower.split()]
            wake_present = self.wake_word in tokens

        if wake_present:
            self._armed_until = now + self.command_window_s
            remainder = self._strip_wake_word(raw)
            return remainder or None

        # If we're armed, pass through.
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
    ):
        self.on_text = on_text
        self.config = config or VoiceConfig()
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._running = threading.Event()
        self._gate = WakeWordGate(wake_word=wake_word, command_window_s=command_window_s)
        self._require_wake_word = True
        self._gate_lock = threading.Lock()

    def set_wake_word_required(self, required: bool) -> None:
        with self._gate_lock:
            self._require_wake_word = required
            if required:
                self._gate.disarm()

    @property
    def is_running(self) -> bool:
        return self._running.is_set()

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.0)

    def _run(self) -> None:
        model_path = resolve_vosk_model_path()
        if not model_path:
            # No model configured; do nothing.
            return

        model = Model(str(model_path))
        rec = KaldiRecognizer(model, self.config.sample_rate)
        rec.SetWords(False)

        q: "queue.Queue[np.ndarray]" = queue.Queue()

        def callback(indata, frames, time, status):  # noqa: ANN001
            if status:
                return
            q.put(indata.copy().reshape(-1))

        self._running.set()
        try:
            try:
                stream = sd.InputStream(
                    samplerate=self.config.sample_rate,
                    channels=1,
                    dtype="int16",
                    callback=callback,
                    device=self.config.device,
                    blocksize=8000,
                )
            except Exception:
                # If audio init fails (no mic permission/device), fail silently.
                return

            with stream:
                while not self._stop.is_set():
                    try:
                        data = q.get(timeout=0.2)
                    except queue.Empty:
                        continue

                    if rec.AcceptWaveform(data.tobytes()):
                        result = json.loads(rec.Result() or "{}")
                        text = (result.get("text") or "").strip()
                        if not text:
                            continue
                        with self._gate_lock:
                            require_wake = self._require_wake_word
                        cmd = self._gate.feed(text, require_wake_word=require_wake)
                        if cmd:
                            self.on_text(cmd)
        finally:
            self._running.clear()

