from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


DEFAULT_VOSK_MODEL_DIR = Path.home() / ".quest_assistant" / "models" / "vosk-model-small-en-us-0.15"


@dataclass(frozen=True)
class VoiceSTTConfig:
    """Environment-driven voice pipeline settings."""

    backend: str = "vosk"  # vosk | whisper | ab
    sample_rate: int = 16000
    device: int | None = None

    # VAD / end-of-utterance
    vad_frame_ms: int = 30
    vad_silence_ms: int = 700
    vad_min_speech_ms: int = 200
    vad_max_utterance_s: float = 18.0
    vad_energy_threshold: float = 320.0  # RMS on int16 mono; raise if noisy room

    # Vosk
    vosk_model_path: Path | None = None

    # faster-whisper (optional)
    whisper_model: str = "tiny.en"
    whisper_compute_type: str = "int8"
    whisper_device: str = "cpu"
    whisper_beam_size: int = 1


def load_voice_stt_config() -> VoiceSTTConfig:
    backend = os.environ.get("JARVIS_STT_BACKEND", "vosk").strip().lower()
    if backend not in {"vosk", "whisper", "ab"}:
        backend = "vosk"

    device_raw = os.environ.get("JARVIS_AUDIO_DEVICE", "").strip()
    device: int | None = None
    if device_raw.isdigit():
        device = int(device_raw)

    vosk_path = resolve_vosk_model_path()

    return VoiceSTTConfig(
        backend=backend,
        sample_rate=int(os.environ.get("JARVIS_STT_SAMPLE_RATE", "16000")),
        device=device,
        vad_frame_ms=int(os.environ.get("JARVIS_VAD_FRAME_MS", "30")),
        vad_silence_ms=int(os.environ.get("JARVIS_VAD_SILENCE_MS", "700")),
        vad_min_speech_ms=int(os.environ.get("JARVIS_VAD_MIN_SPEECH_MS", "200")),
        vad_max_utterance_s=float(os.environ.get("JARVIS_VAD_MAX_UTTERANCE_S", "18")),
        vad_energy_threshold=float(os.environ.get("JARVIS_VAD_ENERGY_THRESHOLD", "320")),
        vosk_model_path=vosk_path,
        whisper_model=os.environ.get("JARVIS_STT_WHISPER_MODEL", "tiny.en"),
        whisper_compute_type=os.environ.get("JARVIS_STT_WHISPER_COMPUTE", "int8"),
        whisper_device=os.environ.get("JARVIS_STT_WHISPER_DEVICE", "cpu"),
        whisper_beam_size=int(os.environ.get("JARVIS_STT_WHISPER_BEAM", "1")),
    )


def resolve_vosk_model_path() -> Path | None:
    env = os.environ.get("VOSK_MODEL_PATH", "").strip()
    if env:
        path = Path(env).expanduser()
        if path.exists():
            return path
    if DEFAULT_VOSK_MODEL_DIR.exists():
        return DEFAULT_VOSK_MODEL_DIR
    return None
