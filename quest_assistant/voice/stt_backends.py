from __future__ import annotations

import json
import threading
import time
from abc import ABC, abstractmethod
from typing import Optional

import numpy as np

from quest_assistant.diagnostics import log_diagnostic
from quest_assistant.monitor.logger import log_voice
from quest_assistant.voice.config import VoiceSTTConfig

_VOSK_MODEL = None
_VOSK_LOCK = threading.Lock()
_WHISPER_MODEL = None
_WHISPER_LOCK = threading.Lock()


class SpeechToTextBackend(ABC):
    name: str = "base"

    @abstractmethod
    def transcribe(self, audio: np.ndarray, *, sample_rate: int) -> str:
        """Transcribe one utterance (int16 mono PCM)."""


class VoskBackend(SpeechToTextBackend):
    name = "vosk"

    def __init__(self, config: VoiceSTTConfig) -> None:
        self._config = config
        if not config.vosk_model_path:
            raise FileNotFoundError(
                "Vosk model not found. Run download_vosk_model.ps1 or set VOSK_MODEL_PATH."
            )

    def transcribe(self, audio: np.ndarray, *, sample_rate: int) -> str:
        from vosk import KaldiRecognizer

        model = _load_vosk_model(self._config)
        rec = KaldiRecognizer(model, sample_rate)
        rec.SetWords(False)
        pcm = np.asarray(audio, dtype=np.int16).tobytes()
        if rec.AcceptWaveform(pcm):
            result = json.loads(rec.Result() or "{}")
        else:
            result = json.loads(rec.FinalResult() or "{}")
        return (result.get("text") or "").strip()


class WhisperBackend(SpeechToTextBackend):
    name = "whisper"

    def __init__(self, config: VoiceSTTConfig) -> None:
        self._config = config

    def transcribe(self, audio: np.ndarray, *, sample_rate: int) -> str:
        model = _load_whisper_model(self._config)
        samples = np.asarray(audio, dtype=np.float32) / 32768.0
        started = time.perf_counter()
        segments, _info = model.transcribe(
            samples,
            language="en",
            beam_size=self._config.whisper_beam_size,
            vad_filter=False,
            condition_on_previous_text=False,
        )
        text = " ".join(seg.text.strip() for seg in segments if seg.text.strip())
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        log_voice(f"whisper transcribed in {elapsed_ms:.0f}ms len={len(text)}")
        return text.strip()


class ABCompareBackend(SpeechToTextBackend):
    """Run Vosk (primary) and Whisper (secondary); log both for tuning."""

    name = "ab"

    def __init__(self, config: VoiceSTTConfig) -> None:
        self._vosk = VoskBackend(config)
        self._whisper: Optional[WhisperBackend] = None
        try:
            self._whisper = WhisperBackend(config)
        except Exception as exc:
            log_diagnostic("voice", "A/B whisper backend unavailable", exc=exc)

    def transcribe(self, audio: np.ndarray, *, sample_rate: int) -> str:
        vosk_text = ""
        whisper_text = ""
        vosk_ms = 0.0
        whisper_ms = 0.0

        t0 = time.perf_counter()
        try:
            vosk_text = self._vosk.transcribe(audio, sample_rate=sample_rate)
        except Exception as exc:
            log_diagnostic("voice", "A/B vosk failed", exc=exc)
        vosk_ms = (time.perf_counter() - t0) * 1000.0

        if self._whisper is not None:
            t1 = time.perf_counter()
            try:
                whisper_text = self._whisper.transcribe(audio, sample_rate=sample_rate)
            except Exception as exc:
                log_diagnostic("voice", "A/B whisper failed", exc=exc)
            whisper_ms = (time.perf_counter() - t1) * 1000.0

        log_voice(
            f"ab_compare vosk={vosk_text!r} ({vosk_ms:.0f}ms) "
            f"whisper={whisper_text!r} ({whisper_ms:.0f}ms)"
        )
        return vosk_text or whisper_text


def create_stt_backend(config: VoiceSTTConfig) -> SpeechToTextBackend:
    backend = config.backend
    if backend == "whisper":
        return WhisperBackend(config)
    if backend == "ab":
        return ABCompareBackend(config)
    return VoskBackend(config)


def _load_vosk_model(config: VoiceSTTConfig):
    global _VOSK_MODEL
    with _VOSK_LOCK:
        if _VOSK_MODEL is None:
            from vosk import Model

            _VOSK_MODEL = Model(str(config.vosk_model_path))
        return _VOSK_MODEL


def _load_whisper_model(config: VoiceSTTConfig):
    global _WHISPER_MODEL
    with _WHISPER_LOCK:
        if _WHISPER_MODEL is None:
            try:
                from faster_whisper import WhisperModel
            except ImportError as exc:
                raise ImportError(
                    "faster-whisper is not installed. Run: pip install faster-whisper"
                ) from exc
            log_voice(
                f"loading whisper model={config.whisper_model} "
                f"compute={config.whisper_compute_type} device={config.whisper_device}"
            )
            _WHISPER_MODEL = WhisperModel(
                config.whisper_model,
                device=config.whisper_device,
                compute_type=config.whisper_compute_type,
            )
        return _WHISPER_MODEL
