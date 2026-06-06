from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ResearchPlan:
    """Multi-step research flow: search → summarize → optional quest."""

    query: str
    add_quest: bool = True
    quest_hint: str = ""
    source_text: str = ""


@dataclass
class PlannerResult:
    ok: bool
    query: str = ""
    summary: str = ""
    quest_title: str = ""
    error: str = ""
