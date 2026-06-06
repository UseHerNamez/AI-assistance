from __future__ import annotations

import queue
import re
import threading
import time
from dataclasses import dataclass
from typing import Callable, Optional

import numpy as np
import sounddevice as sd

from quest_assistant.diagnostics import log_diagnostic
from quest_assistant.monitor.logger import log_voice
from quest_assistant.voice.config import VoiceSTTConfig, load_voice_stt_config
from quest_assistant.voice.pipeline import SpeechPipeline
from quest_assistant.voice.stt_backends import create_stt_backend

_AUDIO_QUEUE_MAX = 32
_THREAD_JOIN_S = 3.0
_DEFAULT_COMMAND_WINDOW_S = 30.0
_BACKGROUND_COMMAND_WINDOW_S = 25.0

_WAKE_ALIASES = frozenset(
    {
        "jarvis",
        "jervis",
        "gervais",
        "chavis",
        "jarvis's",
        "javis",
        "travis",
        "jarves",
        "gervis",
    }
)
_WAKE_TOKEN_RE = re.compile(
    r"\b(?:jarvis|jervis|gervais|chavis|javis|travis|jarves|gervis|jarvis's)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class VoiceConfig:
    sample_rate: int = 16000
    device: Optional[int] = None


class WakeWordGate:
    """
    Filters recognized speech so commands only flow after the wake word.

    Follow-up utterances in the command window do not need "Jarvis" again.
    Background mode still requires "Jarvis" to start a session, but allows
    short follow-ups when speech-to-text splits one command across utterances.
    """

    def __init__(self, wake_word: str = "jarvis", command_window_s: float = 8.0) -> None:
        self.wake_word = wake_word.strip().lower()
        self.command_window_s = float(command_window_s)
        self._armed_until = 0.0

    def disarm(self) -> None:
        self._armed_until = 0.0

    def touch_command_window(self) -> None:
        """Extend the follow-up window after a successful command."""
        if self.command_window_s > 0:
            self._armed_until = time.monotonic() + self.command_window_s

    def _wake_present(self, raw: str) -> bool:
        if not self.wake_word:
            return True
        lower = raw.lower()
        tokens = [t.strip(".,!?;:") for t in lower.split()]
        for token in tokens:
            if token == self.wake_word or token in _WAKE_ALIASES:
                return True
        if _WAKE_TOKEN_RE.search(lower):
            return True
        collapsed = re.sub(r"[^a-z']", "", lower)
        return any(alias in collapsed for alias in ("jarvis", "jervis", "javis", "travis"))

    def _strip_wake_word(self, raw: str) -> Optional[str]:
        if not self.wake_word:
            return raw.strip() or None
        if not self._wake_present(raw):
            return raw.strip() or None
        parts = [
            p
            for p in raw.split()
            if p.strip(".,!?;:").lower() not in ({self.wake_word} | _WAKE_ALIASES)
        ]
        remainder = " ".join(parts).strip(" ,.!?;:\t")
        return remainder or None

    def _show_wake_command(self, raw: str) -> Optional[str]:
        from quest_assistant.parser import resolve_show_command

        return resolve_show_command(raw)

    def feed(
        self,
        text: str,
        *,
        require_wake_word: bool = True,
        background_mode: bool = False,
    ) -> Optional[str]:
        raw = (text or "").strip()
        if not raw:
            return None

        if not require_wake_word:
            return self._strip_wake_word(raw) or raw

        if not self.wake_word:
            return raw

        now = time.monotonic()

        # Summon phrases work even when hidden — STT often drops "Jarvis" on these.
        show_cmd = self._show_wake_command(raw)
        if show_cmd:
            self._armed_until = now + self.command_window_s
            return show_cmd

        if self._wake_present(raw):
            self._armed_until = now + self.command_window_s
            remainder = self._strip_wake_word(raw)
            if not remainder:
                return "wake up"
            return remainder

        if now <= self._armed_until:
            return raw

        if require_wake_word and re.search(
            r"\b(?:open\s*up|wake\s*up|wakeup|jarvis|jervis|show\s+up)\b", raw, re.IGNORECASE
        ):
            log_voice(f"wake_gate dropped possible summon {raw!r}")

        return None


class VoiceListener:
    """
    Always-listening local speech-to-text.

    Uses energy VAD for end-of-utterance, then Vosk (default) or faster-whisper.
    Configure via JARVIS_STT_BACKEND=vosk|whisper|ab
    """

    def __init__(
        self,
        on_text: Callable[[str], None],
        config: VoiceConfig | None = None,
        *,
        wake_word: str = "jarvis",
        command_window_s: float = _DEFAULT_COMMAND_WINDOW_S,
        on_error: Optional[Callable[[str], None]] = None,
        stt_config: VoiceSTTConfig | None = None,
    ):
        self.on_text = on_text
        self.on_error = on_error
        self.config = config or VoiceConfig()
        self._stt_config = stt_config
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._running = threading.Event()
        self._gate = WakeWordGate(wake_word=wake_word, command_window_s=command_window_s)
        self._require_wake_word = True
        self._background_mode = False
        self._gate_lock = threading.Lock()
        self._pipeline: Optional[SpeechPipeline] = None
        self._results_muted = False
        self._generation = 0
        self.last_error: Optional[str] = None

    def set_wake_gate_mode(
        self,
        *,
        require_wake_word: bool,
        background_mode: bool = False,
        command_window_s: float | None = None,
    ) -> None:
        with self._gate_lock:
            entering_wake_required = require_wake_word and not self._require_wake_word
            self._require_wake_word = require_wake_word
            self._background_mode = background_mode
            if command_window_s is not None:
                self._gate.command_window_s = float(command_window_s)
            if entering_wake_required:
                self._gate.disarm()

    def set_wake_word_required(self, required: bool) -> None:
        self.set_wake_gate_mode(require_wake_word=required, background_mode=False)

    def touch_command_window(self) -> None:
        with self._gate_lock:
            self._gate.touch_command_window()

    def set_results_muted(self, muted: bool) -> None:
        with self._gate_lock:
            self._results_muted = muted

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
        self._pipeline = None

    def restart(self) -> None:
        if self.is_starting:
            return
        self.stop()
        self._stop.clear()
        self.start()

    def _emit_transcript(self, text: str) -> None:
        text = (text or "").strip()
        if not text:
            return
        with self._gate_lock:
            require_wake = self._require_wake_word
            background_mode = self._background_mode
        cmd = self._gate.feed(
            text,
            require_wake_word=require_wake,
            background_mode=background_mode,
        )
        if not cmd:
            return
        with self._gate_lock:
            if self._results_muted:
                from quest_assistant.memory.detect import looks_like_memory_panel_intent
                from quest_assistant.parser import parse_show_intent

                if not parse_show_intent(cmd) and not looks_like_memory_panel_intent(cmd):
                    return
        self.on_text(cmd)

    def _run(self, generation: int) -> None:
        if generation != self._generation:
            return

        stt_config = self._stt_config or load_voice_stt_config()
        stt_config = VoiceSTTConfig(
            backend=stt_config.backend,
            sample_rate=self.config.sample_rate,
            device=self.config.device if self.config.device is not None else stt_config.device,
            vad_frame_ms=stt_config.vad_frame_ms,
            vad_silence_ms=stt_config.vad_silence_ms,
            vad_min_speech_ms=stt_config.vad_min_speech_ms,
            vad_max_utterance_s=min(stt_config.vad_max_utterance_s, 18.0),
            vad_energy_threshold=stt_config.vad_energy_threshold,
            vosk_model_path=stt_config.vosk_model_path,
            whisper_model=stt_config.whisper_model,
            whisper_compute_type=stt_config.whisper_compute_type,
            whisper_device=stt_config.whisper_device,
            whisper_beam_size=stt_config.whisper_beam_size,
        )

        if stt_config.backend in {"vosk", "ab"} and not stt_config.vosk_model_path:
            self._report_error(
                "Speech model not found. Run download_vosk_model.ps1 or set VOSK_MODEL_PATH."
            )
            return

        try:
            backend = create_stt_backend(stt_config)
            pipeline = SpeechPipeline(stt_config, backend)
        except FileNotFoundError as exc:
            self._report_error(str(exc), exc=exc)
            return
        except ImportError as exc:
            self._report_error(str(exc), exc=exc)
            return
        except Exception as exc:
            self._report_error("Failed to load speech backend.", exc=exc)
            return

        if generation != self._generation or self._stop.is_set():
            return

        self._pipeline = pipeline
        log_voice(
            f"pipeline start backend={pipeline.backend_name} "
            f"vad_silence_ms={stt_config.vad_silence_ms} "
            f"vad_threshold={stt_config.vad_energy_threshold}"
        )

        audio_q: queue.Queue[np.ndarray] = queue.Queue(maxsize=_AUDIO_QUEUE_MAX)

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
                blocksize=int(self.config.sample_rate * 0.05),
            )
        except Exception as exc:
            self._pipeline = None
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
                        for text in pipeline.feed_audio(data):
                            self._emit_transcript(text)
                    except Exception as exc:
                        log_diagnostic("voice", "recognition step failed", exc=exc)
        finally:
            try:
                if pipeline is not None:
                    tail = pipeline.flush()
                    if tail:
                        self._emit_transcript(tail)
            except Exception as exc:
                log_diagnostic("voice", "pipeline flush failed", exc=exc)
            self._pipeline = None
            self._running.clear()
