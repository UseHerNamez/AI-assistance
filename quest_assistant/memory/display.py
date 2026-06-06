from __future__ import annotations

from dataclasses import dataclass

from quest_assistant.memory.store import EpisodicEntry, MemoryFact, MemoryStore

_PREF_LABELS = {
    "search_engine": "Search engine",
    "browser_hint": "Browser",
    "user_address": "Calls you",
    "tts_voice": "Voice",
}


@dataclass(frozen=True)
class MemoryDisplaySection:
    title: str
    bullets: list[str]


def format_display_sections(store: MemoryStore) -> list[MemoryDisplaySection]:
    """Human-readable memory sections for the UI panel."""
    sections: list[MemoryDisplaySection] = []

    prefs = store.list_prefs()
    if prefs:
        bullets = [
            f"{_pref_label(key)}: {value}"
            for key, value in sorted(prefs.items())
        ]
        sections.append(MemoryDisplaySection("Preferences", bullets))

    facts = store.list_facts(limit=24)
    if facts:
        sections.append(MemoryDisplaySection("Facts", [_format_fact(f) for f in facts]))

    episodic = store.recent_episodic(limit=16)
    if episodic:
        sections.append(
            MemoryDisplaySection("Recent activity", [_format_episodic(e) for e in episodic])
        )

    return sections


def format_display_plain(store: MemoryStore) -> str:
    sections = format_display_sections(store)
    if not sections:
        return "Nothing stored yet. Try:\n• Remember my browser is Firefox\n• My name is Alex"
    parts: list[str] = []
    for section in sections:
        parts.append(section.title)
        parts.extend(f"• {line}" for line in section.bullets)
        parts.append("")
    return "\n".join(parts).strip()


def _pref_label(key: str) -> str:
    return _PREF_LABELS.get(key, key.replace("_", " ").strip().title())


def _format_fact(fact: MemoryFact) -> str:
    if fact.key:
        return f"{fact.category} / {fact.key}: {fact.value}"
    return f"{fact.category}: {fact.value}"


def _format_episodic(entry: EpisodicEntry) -> str:
    kind = entry.kind.replace("_", " ").strip()
    if kind and kind.lower() not in entry.summary.lower():
        return f"{kind}: {entry.summary}"
    return entry.summary
