"""Speech capture, VAD, and pluggable STT backends."""

from quest_assistant.voice.config import VoiceSTTConfig, load_voice_stt_config
from quest_assistant.voice.listener import VoiceListener, WakeWordGate, VoiceConfig
from quest_assistant.voice.vad import UtteranceDetector

__all__ = [
    "UtteranceDetector",
    "VoiceConfig",
    "VoiceListener",
    "VoiceSTTConfig",
    "WakeWordGate",
    "load_voice_stt_config",
]
