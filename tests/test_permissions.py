"""Tests for permission policy (no Qt)."""

from __future__ import annotations

import unittest

from quest_assistant.core.types import RiskLevel
from quest_assistant.intent import tools as T
from quest_assistant.permissions.policy import (
    PermissionSession,
    effective_risk,
    needs_confirmation,
    partition_calls,
)
from quest_assistant.intent.tools import tool


class PermissionsPolicyTests(unittest.TestCase):
    def test_delete_is_medium(self) -> None:
        call = tool(T.TOOL_DELETE_TASK, number=3)
        self.assertEqual(effective_risk(call), RiskLevel.MEDIUM)

    def test_add_is_low(self) -> None:
        call = tool(T.TOOL_CREATE_TASK, titles=["wash dishes"])
        self.assertEqual(effective_risk(call), RiskLevel.LOW)
        self.assertFalse(needs_confirmation(call, PermissionSession()))

    def test_quit_is_high(self) -> None:
        call = tool(T.TOOL_QUIT)
        self.assertEqual(effective_risk(call), RiskLevel.HIGH)
        self.assertTrue(needs_confirmation(call, PermissionSession(medium_confirmed=True)))

    def test_medium_once_per_session(self) -> None:
        session = PermissionSession()
        delete = tool(T.TOOL_DELETE_TASK, number=1)
        self.assertTrue(needs_confirmation(delete, session))
        session.medium_confirmed = True
        self.assertFalse(needs_confirmation(delete, session))

    def test_partition_mixed_batch(self) -> None:
        session = PermissionSession()
        calls = [
            tool(T.TOOL_CREATE_TASK, titles=["a"]),
            tool(T.TOOL_DELETE_TASK, number=2),
        ]
        run_now, prompt = partition_calls(calls, session)
        self.assertEqual(len(run_now), 1)
        self.assertEqual(run_now[0].name, T.TOOL_CREATE_TASK)
        self.assertEqual(len(prompt), 1)
        self.assertEqual(prompt[0].name, T.TOOL_DELETE_TASK)


if __name__ == "__main__":
    unittest.main()
