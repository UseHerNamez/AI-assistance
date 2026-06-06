"""
Backward-compatible imports for the voice stack.

Implementation: quest_assistant.voice.*
"""

from __future__ import annotations

from quest_assistant.voice.config import resolve_vosk_model_path
from quest_assistant.voice.listener import VoiceConfig, VoiceListener, WakeWordGate

__all__ = ["VoiceConfig", "VoiceListener", "WakeWordGate", "resolve_vosk_model_path"]
