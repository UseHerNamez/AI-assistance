from __future__ import annotations

import re
from typing import Literal, Optional

from quest_assistant.parser import normalize_voice_command

MemoryIntent = Literal["show", "hide"]

_RE_SHOW_MEMORY = re.compile(
    r"(?:"
    r"\b(?:what(?:'s|\s+is|\s+do\s+you)\s+remember|"
    r"what(?:'s|\s+is|\s+were)\s+we\s+(?:doing|working\s+on|talking\s+about)|"
    r"what(?:'s|\s+is)\s+the\s+last\s+thing|"
    r"what\s+did\s+you\s+(?:save|store|keep|note)|"
    r"what\s+have\s+you\s+(?:saved|stored|remembered|noted)|"
    r"what\s+do\s+you\s+know\s+about\s+me)\b|"
    r"show(?:\s+me)?(?:\s+your)?\s+memory\b|"
    r"show(?:\s+me)?\s+what\s+you\s+(?:remember|know|saved|stored)\b|"
    r"show(?:\s+me)?\s+(?:your\s+)?(?:memories|memory\s+(?:tab|panel|bank|log|section))\b|"
    r"(?:open|view|see|display|pull\s+up|bring\s+up|read|check)\s+(?:your\s+|the\s+|my\s+)?"
    r"(?:memory(?:\s+(?:tab|panel|bank|log|section))?|memories)\b|"
    r"memory\s+(?:panel|tab|bank|log|section)\b|"
    r"(?:tell|read)\s+me\s+(?:your\s+)?(?:memory|memories)\b|"
    r"let\s+me\s+see\s+(?:your\s+)?(?:memory|memories|what\s+you\s+remember)\b"
    r")",
    re.IGNORECASE,
)

_RE_HIDE_MEMORY = re.compile(
    r"(?:"
    r"(?:hide|close|closed|dismiss|shut|minimize|collapse)\s+(?:the\s+|your\s+|my\s+|that\s+)?"
    r"(?:memory(?:\s+(?:panel|tab|bank|log|section))?|memories)\b|"
    r"(?:hide|close|closed|dismiss|shut)\s+(?:the\s+|your\s+)?(?:memory\s+)?(?:panel|tab)\b|"
    r"(?:put|get)\s+(?:the\s+)?memory\s+(?:away|back)\b|"
    r"close\s+(?:that|the)\s+(?:memory\s+)?tab\b|"
    r"(?:i\s+)?(?:do\s+not|don't|dont)\s+need\s+(?:the\s+)?memory\s+(?:tab|panel)\b"
    r")",
    re.IGNORECASE,
)

_RE_PANEL_CLOSE = re.compile(
    r"^\s*(?:"
    r"(?:close|hide|dismiss|shut|minimize|collapse)\s+"
    r"(?:(?:the\s+|that\s+|this\s+|your\s+|my\s+)?"
    r"(?:memory(?:\s+(?:tab|panel|bank|section))?|memories|tab|panel|it|that|this))"
    r"|(?:close|hide|dismiss|shut|minimize|collapse))"
    r"(?:\s+(?:now|please|sir))?\s*[.!,?]*\s*$",
    re.IGNORECASE,
)

_RE_HIDE_TRAILING = re.compile(
    r"\s+(?:now|please|sir|thanks|thank\s+you)\s*$",
    re.IGNORECASE,
)

_RE_MEMORY_TOPIC = re.compile(
    r"\b(?:memory|memories|remember|recall|saved|stored)\b",
    re.IGNORECASE,
)

_RE_INGEST_REMEMBER = re.compile(
    r"^\s*remember\s+(?:that\s+)?(?!\b(?:about|anything|what|if)\b)",
    re.IGNORECASE,
)

_RE_SHOW_VERBS = re.compile(
    r"\b(?:open|show|see|view|display|pull\s+up|bring\s+up|read|check|tell\s+me|what)\b",
    re.IGNORECASE,
)

_RE_HIDE_VERBS = re.compile(
    r"\b(?:close|closed|hide|dismiss|shut|minimize|collapse|put\s+away|get\s+rid\s+of)\b",
    re.IGNORECASE,
)

_RE_BARE_MEMORY = re.compile(
    r"^\s*(?:the\s+)?memory(?:\s+(?:tab|panel|bank|section))?\s*[.!,?]*\s*$",
    re.IGNORECASE,
)

_RE_OPEN_APP_AFTER_CLOSE = re.compile(
    r"\b(?:open|launch|start)\s+(?!memory|memories|the\s+memory|your\s+memory)\w",
    re.IGNORECASE,
)


def _normalize(text: str) -> str:
    raw = normalize_voice_command(text) or (text or "").strip()
    asr_fixes = (
        (r"\bclosed\b", "close"),
        (r"\bcloze\b", "close"),
        (r"\bclothes\b", "close"),
        (r"\bmemories\b", "memory"),
        (r"\brecall\b", "remember"),
    )
    for pattern, repl in asr_fixes:
        raw = re.sub(pattern, repl, raw, flags=re.IGNORECASE)
    return raw.strip()


def parse_show_memory_intent(text: str, *, memory_panel_open: bool = False) -> bool:
    return resolve_memory_intent(text, memory_panel_open=memory_panel_open) == "show"


def parse_hide_memory_intent(text: str, *, memory_panel_open: bool = False) -> bool:
    return resolve_memory_intent(text, memory_panel_open=memory_panel_open) == "hide"


def looks_like_memory_panel_intent(text: str, *, memory_panel_open: bool = False) -> bool:
    return resolve_memory_intent(text, memory_panel_open=memory_panel_open) is not None


def resolve_memory_intent(text: str, *, memory_panel_open: bool = False) -> Optional[MemoryIntent]:
    raw = _normalize(text)
    if not raw:
        return None

    hide_raw = _RE_HIDE_TRAILING.sub("", raw).strip()

    if memory_panel_open and not _RE_OPEN_APP_AFTER_CLOSE.search(hide_raw):
        if _RE_PANEL_CLOSE.match(hide_raw):
            return "hide"

    if _RE_HIDE_MEMORY.search(hide_raw):
        return "hide"
    if _RE_SHOW_MEMORY.search(raw):
        return "show"
    if _RE_BARE_MEMORY.match(raw):
        return "show"

    if _RE_INGEST_REMEMBER.match(raw) and "memory" not in raw.lower():
        return None

    if not _RE_MEMORY_TOPIC.search(raw):
        return None

    lower = raw.lower()
    if _RE_HIDE_VERBS.search(lower):
        return "hide"
    if _RE_SHOW_VERBS.search(lower):
        return "show"
    if re.search(r"\bwhat\b", lower) and re.search(r"\bremember\b", lower):
        return "show"
    return None
