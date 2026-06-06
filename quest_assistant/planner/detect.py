from __future__ import annotations

import re
from typing import Optional

from quest_assistant.planner.models import ResearchPlan


_RESEARCH_PREFIX = re.compile(
    r"^(?:please\s+)?(?:jarvis[,]?\s+)?"
    r"(?:research|look\s+into|look\s+up|investigate|find\s+(?:me\s+)?|compare)\s+(.+)$",
    re.IGNORECASE,
)
_ADD_QUEST = re.compile(
    r"\b(?:and\s+)?(?:add\s+(?:a\s+)?quest|make\s+(?:it\s+)?a\s+quest|add\s+to\s+(?:my\s+)?(?:list|quests?))\b",
    re.IGNORECASE,
)
_UNDER_BUDGET = re.compile(
    r"(?P<topic>.+?)\s+under\s+(?P<budget>\$?\d[\d,]*)\s*$",
    re.IGNORECASE,
)


def try_build_research_plan(text: str) -> Optional[ResearchPlan]:
    """
    Detect multi-step research requests, e.g. 'research laptops under $1000'.
    Parser/FX/delete paths should run before this in the router.
    """
    cleaned = " ".join((text or "").split()).strip()
    if len(cleaned) < 12:
        return None

    match = _RESEARCH_PREFIX.match(cleaned)
    if not match:
        return None

    body = match.group(1).strip().rstrip(".!?")
    if len(body) < 4:
        return None

    add_quest = bool(_ADD_QUEST.search(cleaned)) or bool(
        re.search(r"\bunder\s+\$?\d", body, re.IGNORECASE)
    )

    quest_hint = body
    budget_match = _UNDER_BUDGET.match(body)
    if budget_match:
        topic = budget_match.group("topic").strip()
        budget = budget_match.group("budget").strip()
        query = f"{topic} under {budget}"
        quest_hint = f"Research {topic} ({budget})"
    else:
        query = body

    return ResearchPlan(
        query=query,
        add_quest=add_quest,
        quest_hint=quest_hint[:120],
        source_text=cleaned,
    )
