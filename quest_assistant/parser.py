from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

import dateparser


@dataclass(frozen=True)
class ParsedAction:
    kind: str  # "add" | "add_done" | "complete" | "delete" | "show" | "hide" | "listen_off" | "quit" | "noop"
    title: Optional[str] = None
    due_iso: Optional[str] = None
    raw: Optional[str] = None


_RE_MULTI_SPLIT = re.compile(r"\s*(?:,|;|\band\b|\bthen\b|\balso\b|\bplus\b)\s*", re.IGNORECASE)
_RE_DELETE = re.compile(r"^\s*(delete|remove)\b", re.IGNORECASE)
_RE_ADD = re.compile(r"^\s*(add|new|create)\b", re.IGNORECASE)
_RE_ADD_INTENT = re.compile(
    r"\b(?:"
    r"add|create|new|make|record|log|write\s+down|put\s+down|"
    r"i\s+(?:want|need|would\s+like)\s+to\s+(?:add|create|record|log|write\s+down|put\s+down)|"
    r"can\s+you\s+(?:add|create|record|log|write\s+down|put\s+down)|"
    r"please\s+(?:add|create|record|log|write\s+down|put\s+down)"
    r")\b",
    re.IGNORECASE,
)
_RE_ADD_DONE = re.compile(r"^\s*(stop\s+adding|done\s+adding|finish\s+adding|that's\s+all)\b", re.IGNORECASE)
_RE_ADD_PREFIX = re.compile(
    r"^\s*(?:"
    r"i\s+(?:want|need|would\s+like)\s+to\s+(?:add|create|record|log|write\s+down|put\s+down)|"
    r"can\s+you\s+(?:add|create|record|log|write\s+down|put\s+down)|"
    r"please\s+(?:add|create|record|log|write\s+down|put\s+down)|"
    r"add|new|create|make|record|log|write\s+down|put\s+down"
    r")\s*(?:(?:a|the|some|those|these|this|one)\s+)?(?:quests?|missions?|tasks?)?\s*:?\s*",
    re.IGNORECASE,
)
_RE_AT_TIME = re.compile(r"\b(at|@)\s*(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\b", re.IGNORECASE)
_RE_MARK_DONE = re.compile(
    r"^\s*mark(?:\s+the)?(?:\s+quest)?\s+(?P<title>.+?)\s+(?:as\s+)?(?:done|complete|finished)\s*$",
    re.IGNORECASE,
)
_RE_DONE_PREFIX = re.compile(r"^\s*(done|complete|finish)\b", re.IGNORECASE)
_RE_SHOW = re.compile(r"^\s*(?:hey\s+)?(wake\s*up|open\s*up|show|appear)\b", re.IGNORECASE)
_RE_HIDE = re.compile(r"^\s*(sleep|go\s*away|hide|disappear|close)\b", re.IGNORECASE)
_RE_LISTEN_OFF = re.compile(
    r"^\s*("
    r"stop\s+listening|"
    r"mute|"
    r"privacy\s+mode|"
    r"mic\s+off|"
    r"(?:turn\s+off|switch\s+off|disable)\s+(?:the\s+)?(?:mic|microphone)|"
    r"disable\s+(?:the\s+)?mic(?:rophone)?"
    r")\b",
    re.IGNORECASE,
)
_RE_QUIT = re.compile(r"^\s*(quit|exit|shutdown)\b", re.IGNORECASE)
_RE_ENUM_MARKER = re.compile(
    r"\b(?:"
    r"(?:and\s+)?(?:the\s+)?(?:next|another)\s+(?:one|quest|mission|task)?|"
    r"(?:and\s+)?one\s+more\s+(?:quest|mission|task)?|"
    r"first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth|"
    r"one|two|three|four|five|six|seven|eight|nine|ten|"
    r"number\s+\d+|\d+(?:st|nd|rd|th)?"
    r")\s+(?:quest|mission|task)?\s*(?:is|it\s+will\s+be|will\s+be|to|:|-)?\s*",
    re.IGNORECASE,
)
_RE_FILLER_PREFIX = re.compile(
    r"^\s*(?:"
    r"i\s+(?:want|need|would\s+like)\s+to\s+(?:add|create|record|log|write\s+down|put\s+down)\s+|"
    r"can\s+you\s+(?:add|create|record|log|write\s+down|put\s+down)\s+|"
    r"please\s+(?:add|create|record|log|write\s+down|put\s+down)\s+|"
    r"add|new|create|make|record|log|write\s+down|put\s+down|"
    r"these\s+quests?\s+for\s+me|quests?\s+for\s+me|for\s+me|"
    r"(?:a|the|some|those|these|this|one)\s+(?:quest|mission|task)\s+(?:called|named|as|is|to)?|"
    r"this\s+(?:quest|mission|task)|"
    r"(?:and\s+)?(?:the\s+)?(?:next|another)\s+(?:one|quest|mission|task)|"
    r"(?:and\s+)?one\s+more\s+(?:quest|mission|task)|"
    r"that\s+it\s+will\s+be\s+to|it\s+will\s+be\s+to|will\s+be\s+to|"
    r"that\s+it\s+will\s+be|it\s+will\s+be|will\s+be|"
    r"quest\s+to|mission\s+to|task\s+to|"
    r"to"
    r")\s+",
    re.IGNORECASE,
)
_RE_TRAILING_FILLER = re.compile(r"\s+(?:and|then)\s*$", re.IGNORECASE)
_RE_TITLE_EXTRACTORS = (
    re.compile(r"\b(?:called|named|title(?:d)?|as)\s+(.+)$", re.IGNORECASE),
    re.compile(r"\b(?:is|will\s+be|should\s+be)\s+(?:to\s+)?(.+)$", re.IGNORECASE),
    re.compile(r"\bto\s+(.+)$", re.IGNORECASE),
)
_STOPWORDS = {
    "a",
    "an",
    "and",
    "it",
    "my",
    "of",
    "project",
    "that",
    "the",
    "to",
    "will",
}


def extract_add_titles(text: str) -> list[str]:
    raw = (text or "").strip()
    if not raw or not has_add_intent(raw):
        return []

    body = _RE_ADD_PREFIX.sub("", raw, count=1).strip()
    return extract_quest_titles(body)


def extract_quest_titles(text: str) -> list[str]:
    body = (text or "").strip()
    if not body:
        return []

    parts = _split_numbered_items(body)
    if not has_numbered_quest_markers(body) and len(parts) <= 1:
        parts = split_into_items(body)

    return [title for title in (_summarize_title(p) for p in parts) if title]


def normalize_quest_title(text: str) -> str:
    return _summarize_title(text)


def has_numbered_quest_markers(text: str) -> bool:
    return bool(_RE_ENUM_MARKER.search(text or ""))


def has_add_intent(text: str) -> bool:
    return bool(_RE_ADD_INTENT.search(text or ""))


def split_into_items(text: str) -> list[str]:
    text = (text or "").strip()
    if not text:
        return []
    parts = [p.strip() for p in _RE_MULTI_SPLIT.split(text) if p.strip()]
    return parts or [text]


def _split_numbered_items(text: str) -> list[str]:
    first_marker = _RE_ENUM_MARKER.search(text)
    normalized = _RE_ENUM_MARKER.sub("\n", text)
    if first_marker and first_marker.start() > 0 and "\n" in normalized:
        # Drop lead-in words like "these quests for me" before "first/one".
        normalized = normalized[normalized.find("\n") + 1 :]
    return [p.strip(" .,:;-") for p in normalized.splitlines() if p.strip(" .,:;-")]


def _summarize_title(text: str, max_words: int = 8, max_chars: int = 60) -> str:
    title = (text or "").strip(" .,:;-")
    title = _extract_embedded_title(title)
    title = _RE_FILLER_PREFIX.sub("", title).strip(" .,:;-")
    title = _RE_TRAILING_FILLER.sub("", title).strip(" .,:;-")
    title = re.sub(r"\s+", " ", title)
    if not title:
        return ""

    words = title.split()
    if len(words) <= max_words and len(title) <= max_chars:
        return title

    kept: list[str] = []
    for word in words:
        clean = word.strip(".,!?;:").lower()
        if clean in _STOPWORDS and kept:
            continue
        kept.append(word.strip(".,!?;:"))
        if len(kept) >= max_words:
            break

    shortened = " ".join(kept).strip()
    if len(shortened) > max_chars:
        shortened = shortened[: max_chars - 1].rstrip() + "."
    return shortened


def _extract_embedded_title(text: str) -> str:
    lowered = text.lower()
    # Avoid chopping useful infinitives such as "program my game" just because
    # they contain "to" elsewhere in the phrase.
    if len(text.split()) <= 5 and not any(k in lowered for k in ("quest", "mission", "task", "add", "record")):
        return text

    for pattern in _RE_TITLE_EXTRACTORS:
        match = pattern.search(text)
        if match:
            candidate = match.group(1).strip(" .,:;-")
            if candidate:
                return candidate
    return text


def _parse_due_iso(raw: str) -> Optional[str]:
    # Try "today/tomorrow/next monday", etc.
    settings = {
        "PREFER_DATES_FROM": "future",
        "RETURN_AS_TIMEZONE_AWARE": False,
    }
    dt = dateparser.parse(raw, settings=settings)
    if dt:
        return dt.isoformat(timespec="minutes")

    # Lightweight "at 7pm" parsing fallback (assume today).
    m = _RE_AT_TIME.search(raw)
    if not m:
        return None
    hour = int(m.group(2))
    minute = int(m.group(3) or "0")
    ampm = (m.group(4) or "").lower()
    if ampm == "pm" and hour < 12:
        hour += 12
    if ampm == "am" and hour == 12:
        hour = 0
    today = datetime.now().replace(hour=hour, minute=minute, second=0, microsecond=0)
    return today.isoformat(timespec="minutes")


def parse_action(text: str, *, allow_implicit_add: bool = True) -> ParsedAction:
    raw = (text or "").strip()
    if not raw:
        return ParsedAction(kind="noop", raw=text)

    if _RE_QUIT.match(raw):
        return ParsedAction(kind="quit", raw=text)

    if _RE_SHOW.match(raw):
        return ParsedAction(kind="show", raw=text)

    if _RE_HIDE.match(raw):
        return ParsedAction(kind="hide", raw=text)

    if _RE_LISTEN_OFF.match(raw):
        return ParsedAction(kind="listen_off", raw=text)

    if _RE_ADD_DONE.match(raw):
        return ParsedAction(kind="add_done", raw=text)

    if _RE_DELETE.match(raw):
        title = _RE_DELETE.sub("", raw, count=1).strip(" :.-")
        title = re.sub(r"^\s*quest\b", "", title, flags=re.IGNORECASE).strip(" :.-")
        return ParsedAction(kind="delete", title=title or None, raw=text)

    m = _RE_MARK_DONE.match(raw)
    if m:
        title = (m.group("title") or "").strip(" :.-")
        return ParsedAction(kind="complete", title=title or None, raw=text)

    if _RE_DONE_PREFIX.match(raw):
        title = _RE_DONE_PREFIX.sub("", raw, count=1).strip(" :.-")
        title = re.sub(r"^\s*quest\b", "", title, flags=re.IGNORECASE).strip(" :.-")
        return ParsedAction(kind="complete", title=title or None, raw=text)

    # If it's an add/create/record intent, treat it as add.
    title = raw
    if has_add_intent(raw):
        titles = extract_add_titles(raw)
        title = titles[0] if titles else ""
        due_iso = _parse_due_iso(raw)
        return ParsedAction(kind="add", title=title or None, due_iso=due_iso, raw=text)

    if not allow_implicit_add:
        return ParsedAction(kind="noop", raw=text)

    # Typed input can still be used as quick-add text.
    due_iso = _parse_due_iso(raw)
    return ParsedAction(kind="add", title=_summarize_title(title) or None, due_iso=due_iso, raw=text)

