"""Tests for intent router → tool calls."""

from __future__ import annotations

import unittest

from quest_assistant.core.session import SessionContext
from quest_assistant.core.types import RiskLevel, RouteKind
from quest_assistant.intent.router import IntentRouter
from quest_assistant.intent.tools import TOOL_CREATE_TASK, TOOL_DELETE_TASK, TOOL_SET_FX, TOOL_START_ADD


class IntentRouterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.router = IntentRouter()
        self.ctx = SessionContext(
            source="voice",
            jarvis_awake=True,
            listening_requested=True,
            may_use_ollama=False,
        )

    def test_delete_task_four_tools(self) -> None:
        decision = self.router.route("delete task 4", self.ctx)
        self.assertEqual(decision.kind, RouteKind.EXECUTE)
        self.assertEqual(decision.tool_calls[0].name, TOOL_DELETE_TASK)
        self.assertEqual(decision.tool_calls[0].arguments.get("number"), 4)
        self.assertEqual(decision.tool_calls[0].risk, RiskLevel.MEDIUM)

    def test_add_task_starts_mode(self) -> None:
        decision = self.router.route("add task", self.ctx)
        self.assertEqual(decision.kind, RouteKind.EXECUTE)
        self.assertEqual(decision.tool_calls[0].name, TOOL_START_ADD)

    def test_fx_off(self) -> None:
        decision = self.router.route("turn effects off", self.ctx)
        self.assertEqual(decision.kind, RouteKind.EXECUTE)
        self.assertEqual(decision.tool_calls[0].name, TOOL_SET_FX)
        self.assertFalse(decision.tool_calls[0].arguments["enabled"])

    def test_delete_not_llm_when_awake(self) -> None:
        ctx = SessionContext(
            source="voice",
            jarvis_awake=True,
            may_use_ollama=True,
        )
        decision = self.router.route("delete task one", ctx)
        self.assertEqual(decision.kind, RouteKind.EXECUTE)
        self.assertNotEqual(decision.kind, RouteKind.LLM)

    def test_pending_add_creates_task(self) -> None:
        ctx = SessionContext(
            source="voice",
            jarvis_awake=True,
            pending_add=True,
            may_use_ollama=True,
        )
        decision = self.router.route("wash dishes", ctx)
        self.assertEqual(decision.kind, RouteKind.EXECUTE)
        self.assertEqual(decision.tool_calls[0].name, TOOL_CREATE_TASK)
        self.assertIn("wash dishes", decision.tool_calls[0].arguments["titles"])


if __name__ == "__main__":
    unittest.main()
