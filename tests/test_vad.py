"""Tests for energy-based utterance detection."""

from __future__ import annotations

import unittest

import numpy as np

from quest_assistant.voice.vad import UtteranceDetector


def _tone(sample_rate: int, ms: int, *, amplitude: int = 8000) -> np.ndarray:
    n = int(sample_rate * ms / 1000)
    t = np.linspace(0, 2 * np.pi * 440 * n / sample_rate, n, dtype=np.float32)
    return (np.sin(t) * amplitude).astype(np.int16)


def _silence(sample_rate: int, ms: int) -> np.ndarray:
    return np.zeros(int(sample_rate * ms / 1000), dtype=np.int16)


class VADTests(unittest.TestCase):
    def test_detects_short_phrase(self) -> None:
        sr = 16000
        vad = UtteranceDetector(
            sample_rate=sr,
            frame_ms=30,
            silence_ms=300,
            min_speech_ms=150,
            energy_threshold=200.0,
        )
        chunks = [_tone(sr, 400), _silence(sr, 400)]
        completed: list[np.ndarray] = []
        for chunk in chunks:
            completed.extend(vad.feed(chunk))
        self.assertEqual(len(completed), 1)
        self.assertGreater(completed[0].size, sr // 4)

    def test_ignores_brief_noise(self) -> None:
        sr = 16000
        vad = UtteranceDetector(
            sample_rate=sr,
            silence_ms=300,
            min_speech_ms=300,
            energy_threshold=500.0,
        )
        completed: list[np.ndarray] = []
        for chunk in [_tone(sr, 80), _silence(sr, 500)]:
            completed.extend(vad.feed(chunk))
        self.assertEqual(len(completed), 0)


if __name__ == "__main__":
    unittest.main()
