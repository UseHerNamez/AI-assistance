from __future__ import annotations

import time
from typing import Optional

import numpy as np

from quest_assistant.monitor.logger import log_voice
from quest_assistant.voice.config import VoiceSTTConfig
from quest_assistant.voice.stt_backends import SpeechToTextBackend, create_stt_backend
from quest_assistant.voice.vad import UtteranceDetector, VADStats


class SpeechPipeline:
    """VAD-segmented audio → STT backend → final text."""

    def __init__(self, config: VoiceSTTConfig, backend: SpeechToTextBackend) -> None:
        self._config = config
        self._backend = backend
        self._vad = UtteranceDetector(
            sample_rate=config.sample_rate,
            frame_ms=config.vad_frame_ms,
            silence_ms=config.vad_silence_ms,
            min_speech_ms=config.vad_min_speech_ms,
            max_utterance_s=config.vad_max_utterance_s,
            energy_threshold=config.vad_energy_threshold,
        )

    @property
    def backend_name(self) -> str:
        return self._backend.name

    def feed_audio(self, chunk: np.ndarray) -> list[str]:
        """Process a mic chunk; return finalized transcripts (may be empty)."""
        utterances = self._vad.feed(chunk)
        texts: list[str] = []
        for audio in utterances:
            text = self._transcribe_utterance(audio, stats=self._vad.last_stats)
            if text:
                texts.append(text)
        return texts

    def flush(self) -> Optional[str]:
        audio = self._vad.flush()
        if audio is None or audio.size == 0:
            return None
        return self._transcribe_utterance(audio, stats=self._vad.last_stats) or None

    def _transcribe_utterance(self, audio: np.ndarray, *, stats: VADStats) -> str:
        started = time.perf_counter()
        try:
            text = self._backend.transcribe(audio, sample_rate=self._config.sample_rate)
        except Exception:
            raise
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        log_voice(
            f"stt backend={self._backend.name} text={text!r} "
            f"stt_ms={elapsed_ms:.0f} audio_ms={stats.duration_ms:.0f} "
            f"speech_ms={stats.speech_ms:.0f} silence_tail_ms={stats.trailing_silence_ms:.0f}"
        )
        return text.strip()
