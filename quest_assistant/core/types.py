from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


class RouteKind(str, Enum):
    EXECUTE = "execute"
    LLM = "llm"
    PLAN = "plan"
    VISION = "vision"
    TYPED_HINT = "typed_hint"
    CONVERSATION = "conversation"
    COMPOSE = "compose"
    NOOP = "noop"


class RiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


@dataclass(frozen=True)
class ToolCall:
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)
    risk: RiskLevel = RiskLevel.LOW


@dataclass
class ToolResult:
    ok: bool
    spoke: bool = False
    refresh: bool = False
    stop: bool = False


@dataclass
class RouteDecision:
    kind: RouteKind
    route_path: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    footer: Optional[str] = None
    instant: bool = True
    research_plan: Any = None
    vision_prompt: Optional[str] = None
    compose_request: Any = None
