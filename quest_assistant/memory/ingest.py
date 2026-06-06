from __future__ import annotations

import re
from typing import Optional

from quest_assistant.memory.store import MemoryStore

_RE_REMEMBER = re.compile(
    r"^\s*(?:remember|don'?t\s+forget|note)\s+(?:that\s+)?(?P<body>.+?)\s*$",
    re.IGNORECASE,
)
_RE_IS = re.compile(
    r"^(?P<left>.+?)\s+(?:is|are|equals?|=)\s+(?P<right>.+?)\s*$",
    re.IGNORECASE,
)
_RE_MY_NAME = re.compile(r"^\s*my\s+name\s+is\s+(?P<name>.+?)\s*$", re.IGNORECASE)
_RE_CALL_ME = re.compile(r"^\s*call\s+me\s+(?P<name>.+?)\s*$", re.IGNORECASE)
_RE_PREFERRED = re.compile(
    r"^\s*(?:my\s+)?(?:preferred|favourite|favorite)\s+(?P<key>[a-z][a-z0-9_\s-]{1,40}?)\s+is\s+(?P<val>.+?)\s*$",
    re.IGNORECASE,
)

_PREF_KEY_ALIASES = {
    "browser": "browser_hint",
    "web browser": "browser_hint",
    "search": "search_engine",
    "search engine": "search_engine",
    "voice": "tts_voice",
}


def try_ingest_rememberance(memory: MemoryStore, text: str) -> bool:
    """
    Parse simple "remember …" / "my name is …" phrases into prefs/facts.
    Returns True if something was stored (parser can skip LLM for these).
    """
    raw = (text or "").strip()
    if not raw:
        return False

    m = _RE_MY_NAME.match(raw)
    if m:
        name = m.group("name").strip(" .,!?:;")
        if name:
            memory.set_fact("user", name, key="name")
            memory.set_pref("user_address", name.split()[0] if " " not in name else name)
            return True

    m = _RE_CALL_ME.match(raw)
    if m:
        name = m.group("name").strip(" .,!?:;")
        if name and not re.match(r"^(?:a|an|the)\s+", name, re.IGNORECASE):
            memory.set_pref("user_address", name)
            memory.set_fact("user", name, key="call_name")
            return True

    m = _RE_PREFERRED.match(raw)
    if m:
        key = _normalize_pref_key(m.group("key"))
        val = m.group("val").strip(" .,!?:;")
        if key and val:
            memory.set_pref(key, val)
            return True

    m = _RE_REMEMBER.match(raw)
    if not m:
        return False

    body = m.group("body").strip()
    if not body:
        return False

    split = _RE_IS.match(body)
    if split:
        left = split.group("left").strip()
        right = split.group("right").strip(" .,!?:;")
        if left and right:
            return _store_left_right(memory, left, right)

    memory.set_fact("note", body)
    return True


def _store_left_right(memory: MemoryStore, left: str, right: str) -> bool:
    lower = left.lower()
    if lower.startswith("my "):
        left = left[3:].strip()

    person = re.match(r"^(?P<who>[a-z]+)(?:'s|s)?\s+name$", lower)
    if person:
        memory.set_fact("person", right, key=person.group("who"))
        return True

    pref_key = _normalize_pref_key(left)
    if pref_key in _PREF_KEY_ALIASES.values() or pref_key in _PREF_KEY_ALIASES:
        memory.set_pref(_PREF_KEY_ALIASES.get(pref_key, pref_key), right)
        return True

    if " " not in left and len(left) < 24:
        memory.set_pref(pref_key, right)
        return True

    memory.set_fact("general", right, key=left[:48])
    return True


def _normalize_pref_key(raw: str) -> str:
    key = re.sub(r"\s+", "_", (raw or "").strip().lower())
    return _PREF_KEY_ALIASES.get(key, key)
