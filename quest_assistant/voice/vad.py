from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np


@dataclass
class VADStats:
    """Debug snapshot of the last completed utterance."""

    duration_ms: float = 0.0
    speech_ms: float = 0.0
    trailing_silence_ms: float = 0.0
    peak_rms: float = 0.0


class UtteranceDetector:
    """
    Energy-based voice activity detection.

    Buffers audio until trailing silence exceeds ``silence_ms`` after at least
  ``min_speech_ms`` of speech. Caps utterances at ``max_utterance_s``.
    """

    def __init__(
        self,
        *,
        sample_rate: int = 16000,
        frame_ms: int = 30,
        silence_ms: int = 700,
        min_speech_ms: int = 250,
        max_utterance_s: float = 18.0,
        energy_threshold: float = 380.0,
    ) -> None:
        self.sample_rate = sample_rate
        self.frame_samples = max(1, int(sample_rate * frame_ms / 1000))
        self.silence_frames = max(1, int(silence_ms / frame_ms))
        self.min_speech_frames = max(1, int(min_speech_ms / frame_ms))
        self.max_utterance_samples = int(sample_rate * max_utterance_s)
        self.energy_threshold = float(energy_threshold)

        self._carry = np.array([], dtype=np.int16)
        self._buffer: list[np.ndarray] = []
        self._buffer_samples = 0
        self._in_speech = False
        self._speech_frames = 0
        self._silence_run = 0
        self._peak_rms = 0.0
        self.last_stats = VADStats()

    def feed(self, chunk: np.ndarray) -> list[np.ndarray]:
        """Append PCM int16 mono; return zero or more completed utterances."""
        if chunk.size == 0:
            return []
        chunk = np.asarray(chunk, dtype=np.int16).reshape(-1)
        self._carry = np.concatenate([self._carry, chunk])
        completed: list[np.ndarray] = []

        while self._carry.size >= self.frame_samples:
            frame = self._carry[: self.frame_samples]
            self._carry = self._carry[self.frame_samples :]
            utterance = self._process_frame(frame)
            if utterance is not None and utterance.size > 0:
                completed.append(utterance)

        return completed

    def flush(self) -> Optional[np.ndarray]:
        """Force-end an in-progress utterance (e.g. mic stop)."""
        if not self._in_speech or self._speech_frames < self.min_speech_frames:
            self._reset_utterance()
            return None
        return self._finish_utterance()

    def _process_frame(self, frame: np.ndarray) -> Optional[np.ndarray]:
        rms = float(np.sqrt(np.mean(frame.astype(np.float32) ** 2)))
        self._peak_rms = max(self._peak_rms, rms)
        is_speech = rms >= self.energy_threshold

        if is_speech:
            if not self._in_speech:
                self._in_speech = True
                self._buffer.clear()
                self._buffer_samples = 0
                self._speech_frames = 0
                self._silence_run = 0
                self._peak_rms = rms
            self._append_frame(frame)
            self._speech_frames += 1
            self._silence_run = 0
            if self._buffer_samples >= self.max_utterance_samples:
                return self._finish_utterance()
            return None

        if not self._in_speech:
            return None

        self._append_frame(frame)
        self._silence_run += 1
        if self._silence_run >= self.silence_frames and self._speech_frames >= self.min_speech_frames:
            return self._finish_utterance()
        return None

    def _append_frame(self, frame: np.ndarray) -> None:
        self._buffer.append(frame.copy())
        self._buffer_samples += frame.size

    def _finish_utterance(self) -> np.ndarray:
        speech_frames = self._speech_frames
        silence_ms = self._silence_run * (1000.0 * self.frame_samples / self.sample_rate)
        duration_ms = self._buffer_samples * 1000.0 / self.sample_rate
        speech_ms = speech_frames * (1000.0 * self.frame_samples / self.sample_rate)
        self.last_stats = VADStats(
            duration_ms=duration_ms,
            speech_ms=speech_ms,
            trailing_silence_ms=silence_ms,
            peak_rms=self._peak_rms,
        )
        if not self._buffer:
            self._reset_utterance()
            return np.array([], dtype=np.int16)
        audio = np.concatenate(self._buffer)
        self._reset_utterance()
        return audio

    def _reset_utterance(self) -> None:
        self._buffer.clear()
        self._buffer_samples = 0
        self._in_speech = False
        self._speech_frames = 0
        self._silence_run = 0
        self._peak_rms = 0.0
