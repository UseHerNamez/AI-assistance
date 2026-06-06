from __future__ import annotations

import re
from typing import Optional

from quest_assistant.compose.models import ComposeRequest
from quest_assistant.parser import normalize_voice_command

_RE_COMPOSE = re.compile(
    r"(?:"
    r"(?:please\s+|can\s+you\s+|could\s+you\s+|just\s+)?"
    r"(?:write|draft|compose|create|type|prepare)\s+(?:me\s+)?"
    r"(?:(?:a|an|the)\s+)?"
    r"(?:(?P<kind>text(?:\s+file)?|email|e-mail|message|letter|essay|report|document|note)\s+)?"
    r"(?:about|on|regarding)\s+(?P<topic>.+?)"
    r"|"
    r"open\s+(?P<dest>notepad|word(?:\s+document)?|microsoft\s+word|outlook|text(?:\s+file)?|(?:an?\s+)?email)\s+"
    r"(?:and\s+)?(?:write|draft|compose|create|type|prepare)\s+(?:me\s+)?"
    r"(?:(?:a|an|the)\s+)?"
    r"(?:(?P<kind2>text(?:\s+file)?|email|e-mail|message|letter|essay|report|document|note)\s+)?"
    r"(?:about|on|regarding)\s+(?P<topic2>.+?)"
    r")"
    r"(?:\s+(?:please|now|sir|thanks|thank you))?\s*$",
    re.IGNORECASE,
)

_RE_COMPOSE_LOOSE = re.compile(
    r"(?:"
    r"(?:open\s+(?P<dest_loose>notepad|word(?:\s+document)?|microsoft\s+word|outlook|text(?:\s+file)?|email)\s+)?"
    r".*?"
    r"(?:write|draft|compose|create|type|prepare)\s+(?:me\s+)?"
    r"(?:(?:a|an|the)\s+)?"
    r"(?:(?P<kind_loose>text(?:\s+file)?|email|e-mail|message|letter|essay|report|document|note)\s+)?"
    r"(?:about|on|regarding)\s+(?P<topic_loose>.+?)"
    r")"
    r"(?:\s+(?:please|now|sir|thanks|thank you))?\s*$",
    re.IGNORECASE,
)

_RE_COMPOSE_SEARCH = re.compile(
    r"(?:write|draft|compose|create|type|prepare)\s+(?:me\s+)?"
    r"(?:(?:a|an|the)\s+)?"
    r"(?:(?P<kind_search>text(?:\s+file)?|email|e-mail|message|letter|essay|report|document|note)\s+)?"
    r"(?:about|on|regarding)\s+(?P<topic_search>.+?)"
    r"(?:\s+(?:please|now|sir|thanks|thank you))?\s*$",
    re.IGNORECASE,
)

_RE_OPEN_DEST = re.compile(
    r"\b(?:open\s+)?(?:in\s+)?"
    r"(?P<dest>notepad|word(?:\s+document)?|microsoft\s+word|outlook|text(?:\s+file)?|email|e-mail)\b",
    re.IGNORECASE,
)

_RE_TRAILING_DEST = re.compile(
    r"\s+(?:in|using|with|via|on)\s+(?:notepad|word|outlook|email|e-mail|text(?:\s+file)?)\s*$",
    re.IGNORECASE,
)

_RE_TOPIC_TRIM = re.compile(
    r"\s+(?:"
    r"just\s+to\s+(?:test|try|see)(?:\s+(?:it|that|this))?(?:\s+out)?|"
    r"please|now|sir|thanks|thank\s+you"
    r")\s*$",
    re.IGNORECASE,
)

_RE_UTTERANCE_TRIM = re.compile(
    r"\s+(?:just\s+to\s+(?:test|try|see)(?:\s+(?:it|that|this))?(?:\s+out)?)\s*$",
    re.IGNORECASE,
)

_RE_WRITE_VERB = re.compile(
    r"\b(?:write|draft|compose|create|type|prepare)\b",
    re.IGNORECASE,
)


def _normalize_compose_text(text: str) -> str:
    raw = normalize_voice_command(text)
    asr_fixes = (
        (r"\bright me\b", "write me"),
        (r"\bright an\b", "write an"),
        (r"\bright a\b", "write a"),
        (r"\b(?:route|rode|wrote|root)\s+me\b", "write me"),
        (r"\b(?:route|rode|wrote|root)\s+an\b", "write an"),
        (r"\b(?:route|rode|wrote|root)\s+a\b", "write a"),
        (r"\bopen\s+ward\b", "open word"),
        (r"\bopen\s+wore\b", "open word"),
        (r"\bopen\s+words\b", "open word"),
        (r"\bmicrosoft\s+words\b", "microsoft word"),
    )
    for pattern, repl in asr_fixes:
        raw = re.sub(pattern, repl, raw, flags=re.IGNORECASE)
    return _RE_UTTERANCE_TRIM.sub("", raw).strip()


def _resolve_destination(*, dest: Optional[str], kind: Optional[str], raw: str) -> str:
    blob = " ".join(part for part in (dest, kind, raw) if part).lower()
    if any(token in blob for token in ("outlook", "email", "e-mail", "mail message")):
        return "outlook"
    if "word" in blob:
        return "word"
    if any(token in blob for token in ("notepad", "text file", "textfile", "text document", "note")):
        return "notepad"
    if kind and re.search(r"\b(?:email|e-mail|message|letter)\b", kind, re.IGNORECASE):
        return "outlook"
    return "notepad"


def _clean_topic(topic: str, raw: str) -> str:
    cleaned = _RE_TRAILING_DEST.sub("", (topic or "").strip(" .,!?;:"))
    cleaned = _RE_TOPIC_TRIM.sub("", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


def _build_request(
    *,
    dest: Optional[str],
    kind: Optional[str],
    topic: str,
    raw: str,
    original: str,
) -> Optional[ComposeRequest]:
    cleaned_topic = _clean_topic(topic, raw)
    if len(cleaned_topic) < 3:
        return None
    destination = _resolve_destination(dest=dest, kind=kind, raw=raw)
    return ComposeRequest(destination=destination, topic=cleaned_topic, raw=original)


def looks_like_compose_intent(text: str) -> bool:
    raw = _normalize_compose_text(text)
    if not raw:
        return False
    if not _RE_WRITE_VERB.search(raw):
        return False
    if not re.search(r"\b(?:about|on|regarding)\b", raw, re.IGNORECASE):
        return False
    return True


def is_echo_reply(utterance: str, reply: str) -> bool:
    u = " ".join((utterance or "").lower().split())
    r = " ".join((reply or "").lower().split())
    if not u or not r:
        return False
    if u == r:
        return True
    if len(u) >= 12 and (u in r or r in u):
        return True
    if looks_like_compose_intent(utterance) and len(r) >= 12 and len(u) >= 12:
        u_words = set(u.split())
        r_words = set(r.split())
        overlap = len(u_words & r_words) / max(len(u_words), 1)
        if overlap >= 0.65:
            return True
    return False


def _fallback_compose_request(raw: str, original: str) -> Optional[ComposeRequest]:
    if not looks_like_compose_intent(original):
        return None

    search = _RE_COMPOSE_SEARCH.search(raw)
    if not search:
        return None

    dest_match = _RE_OPEN_DEST.search(raw)
    return _build_request(
        dest=dest_match.group("dest") if dest_match else None,
        kind=search.group("kind_search"),
        topic=search.group("topic_search") or "",
        raw=raw,
        original=original,
    )


def parse_compose_request(text: str) -> Optional[ComposeRequest]:
    return resolve_compose_request(text)


def resolve_compose_request(text: str) -> Optional[ComposeRequest]:
    raw = _normalize_compose_text(text)
    if not raw:
        return None

    match = _RE_COMPOSE.match(raw)
    if match:
        return _build_request(
            dest=match.group("dest"),
            kind=match.group("kind") or match.group("kind2"),
            topic=match.group("topic") or match.group("topic2") or "",
            raw=raw,
            original=text,
        )

    loose = _RE_COMPOSE_LOOSE.match(raw)
    if loose:
        return _build_request(
            dest=loose.group("dest_loose"),
            kind=loose.group("kind_loose"),
            topic=loose.group("topic_loose") or "",
            raw=raw,
            original=text,
        )

    return _fallback_compose_request(raw, text)
