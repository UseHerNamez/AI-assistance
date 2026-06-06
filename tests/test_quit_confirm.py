"""Quit must run after the user approves the permission dialog."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from quest_assistant.core.types import RiskLevel
from quest_assistant.db import QuestDB
from quest_assistant.intent import tools as T
from quest_assistant.intent.tools import tool
from quest_assistant.ui.action_host import QuestActionHost
from quest_assistant.ui_widget import QuestWidget, UIState


def _make_widget(db: QuestDB) -> QuestWidget:
    widget = QuestWidget.__new__(QuestWidget)
    widget.db = db
    widget.state = UIState(jarvis_awake=True, listening_requested=True)
    widget._permission_session = __import__(
        "quest_assistant.permissions.policy", fromlist=["PermissionSession"]
    ).PermissionSession()
    widget._last_command_text = ""
    widget._jarvis_say = MagicMock()
    widget._quit_assistance_confirmed = MagicMock()
    return widget


class QuitAfterPermissionTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db = QuestDB(Path(self._tmpdir.name) / "quit.db")
        self.widget = _make_widget(self.db)
        self.host = QuestActionHost(self.widget)

    def tearDown(self) -> None:
        self.db.close()
        self._tmpdir.cleanup()

    @patch.object(QuestWidget, "request_tool_confirmation", return_value=True)
    def test_quit_runs_after_user_allows(self, _confirm: MagicMock) -> None:
        self.widget._last_command_text = "quit assistance"
        result = self.host.execute([tool(T.TOOL_QUIT, risk=RiskLevel.HIGH)], route_path="test")

        self.assertTrue(result.ok)
        self.assertTrue(result.stop)
        self.widget._quit_assistance_confirmed.assert_called_once()

    @patch.object(QuestWidget, "request_tool_confirmation", return_value=True)
    def test_quit_runs_even_when_control_cooldown_active(self, _confirm: MagicMock) -> None:
        """Regression: cooldown from an earlier blocked quit must not block confirmed quit."""
        self.widget._last_control_kind = "quit"
        self.widget._last_control_at = __import__("time").monotonic()
        self.widget._last_command_text = "quit"

        self.host.execute([tool(T.TOOL_QUIT, risk=RiskLevel.HIGH)], route_path="test")

        self.widget._quit_assistance_confirmed.assert_called_once()

    @patch.object(QuestWidget, "request_tool_confirmation", return_value=False)
    def test_quit_skipped_when_user_cancels(self, _confirm: MagicMock) -> None:
        result = self.host.execute([tool(T.TOOL_QUIT, risk=RiskLevel.HIGH)], route_path="test")

        self.assertFalse(result.ok)
        self.widget._quit_assistance_confirmed.assert_not_called()


if __name__ == "__main__":
    unittest.main()
