"""Tests for daily repeating quest tasks."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from quest_assistant.core.session import SessionContext
from quest_assistant.db import QuestDB
from quest_assistant.intent import tools as T
from quest_assistant.intent.parser_route import route_parser_actions
from quest_assistant.parser import (
    extract_daily_add_titles,
    has_daily_add_intent,
    looks_like_daily_typed_quest,
    parse_action,
)


class DailyParserTests(unittest.TestCase):
    def test_daily_add_intent(self) -> None:
        self.assertTrue(has_daily_add_intent("add daily brush teeth"))
        self.assertTrue(has_daily_add_intent("daily workout"))
        self.assertTrue(has_daily_add_intent("brush teeth every day"))
        self.assertFalse(has_daily_add_intent("wash dishes"))

    def test_route_add_daily(self) -> None:
        ctx = SessionContext(
            source="typed",
            jarvis_awake=False,
            listening_requested=True,
            pending_add=False,
            pending_delete=False,
            open_quests="",
            fx_visually_on=False,
            may_use_ollama=True,
            llm_enabled=True,
            last_command_text="",
            voice_filler_words=frozenset(),
        )
        calls = route_parser_actions("add daily exercise", ctx)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0].name, T.TOOL_CREATE_DAILY_TASK)
        self.assertEqual(calls[0].arguments["titles"], ["exercise"])

        calls = route_parser_actions("Daily exercise", ctx)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0].name, T.TOOL_CREATE_DAILY_TASK)

    def test_extract_daily_titles(self) -> None:
        self.assertEqual(extract_daily_add_titles("add daily brush teeth"), ["brush teeth"])
        self.assertEqual(extract_daily_add_titles("daily workout"), ["workout"])
        self.assertEqual(extract_daily_add_titles("meditate every day"), ["meditate"])

    def test_parse_action_add_daily(self) -> None:
        action = parse_action("add daily brush teeth", allow_implicit_add=False)
        self.assertEqual(action.kind, "add_daily")
        self.assertEqual(action.title, "brush teeth")

        action = parse_action("daily workout", allow_implicit_add=True)
        self.assertEqual(action.kind, "add_daily")
        self.assertEqual(action.title, "workout")

    def test_typed_daily_quest(self) -> None:
        self.assertTrue(looks_like_daily_typed_quest("daily stretch"))
        self.assertFalse(looks_like_daily_typed_quest("wash dishes"))


class DailyDBTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db = QuestDB(Path(self._tmpdir.name) / "daily.db")

    def tearDown(self) -> None:
        self.db.close()
        self._tmpdir.cleanup()

    def test_daily_complete_and_rollover(self) -> None:
        task_id = self.db.add_daily_task("stretch")
        assert task_id is not None

        self.db.complete_task(task_id)
        task = self.db.get_task(task_id)
        assert task is not None
        self.assertEqual(task.status, "done")
        self.assertTrue(task.is_daily)

        buckets = self.db.list_quest_buckets()
        self.assertEqual(len(buckets.open_daily), 0)
        self.assertEqual(len(buckets.done_daily), 1)

        with patch("quest_assistant.db.local_today_iso", return_value="2099-01-02"):
            self.db.rollover_daily_tasks()

        task = self.db.get_task(task_id)
        assert task is not None
        self.assertEqual(task.status, "open")
        self.assertIsNone(task.daily_done_on)

        buckets = self.db.list_quest_buckets()
        self.assertEqual(len(buckets.open_daily), 1)
        self.assertEqual(len(buckets.done_daily), 0)

    def test_daily_cannot_delete_from_done(self) -> None:
        task_id = self.db.add_daily_task("journal")
        assert task_id is not None
        self.db.complete_task(task_id)
        self.assertFalse(self.db.can_delete_task(task_id))
        self.assertFalse(self.db.delete_task(task_id))
        self.assertIsNotNone(self.db.get_task(task_id))

    def test_daily_can_delete_from_open(self) -> None:
        task_id = self.db.add_daily_task("journal")
        assert task_id is not None
        self.assertTrue(self.db.can_delete_task(task_id))
        self.assertTrue(self.db.delete_task(task_id))
        self.assertIsNone(self.db.get_task(task_id))

    def test_open_numbering_per_section(self) -> None:
        normal_id = self.db.add_task("normal")
        daily_id = self.db.add_daily_task("daily one")
        assert normal_id is not None and daily_id is not None
        daily_one = self.db.get_open_task_by_number(1, daily=True)
        normal_one = self.db.get_open_task_by_number(1, daily=False)
        assert daily_one is not None and normal_one is not None
        self.assertEqual(daily_one.title, "daily one")
        self.assertEqual(normal_one.title, "normal")
        # Bare numbers prefer the normal section when it has that index.
        bare = self.db.get_open_task_by_number(1)
        assert bare is not None
        self.assertEqual(bare.title, "normal")


if __name__ == "__main__":
    unittest.main()
