from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class SessionContext:
    """Snapshot passed into the intent router (no Qt dependencies)."""

    source: str = "voice"
    jarvis_awake: bool = False
    listening_requested: bool = True
    pending_add: bool = False
    pending_delete: bool = False
    open_quests: list[dict[str, Any]] = field(default_factory=list)
    fx_visually_on: bool = False
    may_use_ollama: bool = False
    llm_enabled: bool = False
    last_command_text: str = ""
    voice_filler_words: frozenset[str] = frozenset()
