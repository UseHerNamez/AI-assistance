"""Integration tests for LLM batch delete/complete (multi task number)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from quest_assistant.db import QuestDB
from quest_assistant.local_llm import LLMAction, LLMResult
from quest_assistant.ui_widget import QuestWidget, UIState


def _make_widget(db: QuestDB) -> QuestWidget:
    widget = QuestWidget.__new__(QuestWidget)
    widget.db = db
    widget.state = UIState(jarvis_awake=True, listening_requested=True)
    widget._pending_voice_add = False
    widget._pending_voice_delete = False
    widget.sfx = MagicMock()
    widget._jarvis_say = MagicMock()
    widget._set_footer_default = MagicMock()
    widget._set_pending_add = MagicMock()
    return widget


class LLMMultiDeleteTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db = QuestDB(Path(self._tmpdir.name) / "llm_batch.db")
        for title in ("alpha", "beta", "gamma", "delta"):
            self.db.add_task(title)
        self.widget = _make_widget(self.db)

    def tearDown(self) -> None:
        self.db.close()
        self._tmpdir.cleanup()

    @patch.object(QuestWidget, "_try_apply_quest_command", return_value=False)
    @patch.object(QuestWidget, "_try_apply_fx_command", return_value=False)
    def test_llm_deletes_tasks_two_and_four_by_number(
        self,
        _fx: MagicMock,
        _quest: MagicMock,
    ) -> None:
        """Regression: batch snapshot must delete original #2 and #4, not #2 then shifted #4."""
        utterance = "delete task 2 and task 4"
        result = LLMResult(
            actions=[
                LLMAction(kind="delete", title="2"),
                LLMAction(kind="delete", title="4"),
            ],
            elapsed_s=0.1,
            model="test",
        )

        handled = self.widget._apply_llm_result(utterance, "voice", result)

        self.assertTrue(handled)
        remaining = [t.title for t in self.db.list_tasks(status="open")]
        self.assertEqual(remaining, ["alpha", "gamma"])
        self.widget._jarvis_say.assert_called_once_with("Deleted 2 quests, sir.")

    @patch.object(QuestWidget, "_try_apply_quest_command", return_value=False)
    @patch.object(QuestWidget, "_try_apply_fx_command", return_value=False)
    def test_llm_multi_delete_without_and_only_first_number(
        self,
        _fx: MagicMock,
        _quest: MagicMock,
    ) -> None:
        """Without multi-delete phrasing, only the first numbered delete action applies."""
        utterance = "delete task 2"
        result = LLMResult(
            actions=[
                LLMAction(kind="delete", title="2"),
                LLMAction(kind="delete", title="4"),
            ],
            elapsed_s=0.1,
            model="test",
        )

        self.widget._apply_llm_result(utterance, "voice", result)

        remaining = [t.title for t in self.db.list_tasks(status="open")]
        self.assertEqual(remaining, ["alpha", "gamma", "delta"])

    @patch.object(QuestWidget, "_try_apply_quest_command", return_value=False)
    @patch.object(QuestWidget, "_try_apply_fx_command", return_value=False)
    def test_llm_completes_tasks_one_and_three_by_number(
        self,
        _fx: MagicMock,
        _quest: MagicMock,
    ) -> None:
        utterance = "mark task 1 and task 3 done"
        result = LLMResult(
            actions=[
                LLMAction(kind="complete", title="1"),
                LLMAction(kind="complete", title="3"),
            ],
            elapsed_s=0.1,
            model="test",
        )

        handled = self.widget._apply_llm_result(utterance, "voice", result)

        self.assertTrue(handled)
        open_titles = [t.title for t in self.db.list_tasks(status="open")]
        done_titles = [t.title for t in self.db.list_tasks(status="done")]
        self.assertEqual(open_titles, ["beta", "delta"])
        self.assertEqual(sorted(done_titles), ["alpha", "gamma"])


if __name__ == "__main__":
    unittest.main()
