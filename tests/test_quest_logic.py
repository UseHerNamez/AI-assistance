"""Tests for quest parser and database edge cases."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from quest_assistant.db import QuestDB
from quest_assistant.parser import (
    looks_like_listen_off,
    looks_like_typed_quest,
    parse_action,
    parse_fx_enabled,
    parse_listen_off_intent,
    parse_open_browser_intent,
    parse_quit_intent,
    parse_web_search_query,
)


class ParserTests(unittest.TestCase):
    def test_open_browser_intent(self) -> None:
        self.assertTrue(parse_open_browser_intent("open the browser"))
        self.assertTrue(parse_action("open browser").kind == "open_browser")

    def test_web_search_intent(self) -> None:
        self.assertEqual(parse_web_search_query("search google for python tutorials"), "python tutorials")
        self.assertEqual(parse_action("search google for cats").kind, "web_search")

    def test_quit_intent(self) -> None:
        self.assertTrue(parse_quit_intent("quit"))
        self.assertTrue(parse_quit_intent("exit"))
        self.assertTrue(parse_quit_intent("shutdown"))
        self.assertTrue(parse_quit_intent("quit assistance"))
        self.assertTrue(parse_quit_intent("shutdown jarvis"))

    def test_add_voice_command(self) -> None:
        action = parse_action("add wash dishes", allow_implicit_add=False)
        self.assertEqual(action.kind, "add")
        self.assertEqual(parse_action("add a task workout").kind, "add")

    def test_typed_quest_heuristic(self) -> None:
        self.assertFalse(looks_like_typed_quest("hello"))
        self.assertFalse(looks_like_typed_quest("what time is it?"))
        self.assertTrue(looks_like_typed_quest("wash dishes"))
        self.assertTrue(looks_like_typed_quest("wash dishes, workout"))

    def test_implicit_add_off_for_chat(self) -> None:
        self.assertEqual(parse_action("hello", allow_implicit_add=False).kind, "noop")
        self.assertEqual(parse_action("wash dishes", allow_implicit_add=True).kind, "add")

    def test_listen_off_intent_strict(self) -> None:
        self.assertTrue(parse_listen_off_intent("mic off"))
        self.assertTrue(parse_listen_off_intent("stop listening"))
        self.assertTrue(parse_listen_off_intent("turn off the microphone"))
        self.assertTrue(parse_listen_off_intent("mute the mic"))
        self.assertTrue(parse_listen_off_intent("can you mute the mic"))
        self.assertTrue(parse_listen_off_intent("could you turn off the microphone"))
        self.assertTrue(parse_listen_off_intent("turn the mic off"))
        self.assertTrue(parse_listen_off_intent("turn the microphone off"))
        self.assertTrue(parse_listen_off_intent("jarvis can you mute the mic"))
        self.assertTrue(parse_listen_off_intent("mute mike"))
        self.assertTrue(parse_listen_off_intent("turn off the mike"))
        self.assertFalse(parse_listen_off_intent("mute"))
        self.assertFalse(parse_listen_off_intent("stop listening to the radio"))
        self.assertFalse(parse_listen_off_intent("don't listen to me anymore"))
        self.assertEqual(parse_action("mic off", allow_implicit_add=False).kind, "listen_off")
        self.assertEqual(parse_action("can you mute the mic", allow_implicit_add=False).kind, "listen_off")

    def test_listen_off_fuzzy(self) -> None:
        self.assertTrue(looks_like_listen_off("turn off the microphone"))
        self.assertTrue(looks_like_listen_off("i need you to mute the microphone"))
        self.assertTrue(looks_like_listen_off("go silent"))
        self.assertTrue(looks_like_listen_off("jarvis stop listening"))
        self.assertFalse(looks_like_listen_off("stop listening to the radio"))

    def test_fx_voice_phrases(self) -> None:
        from quest_assistant.parser import resolve_fx_enabled

        self.assertTrue(parse_fx_enabled("turn effects on"))
        self.assertTrue(parse_fx_enabled("turn the fx on"))
        self.assertFalse(parse_fx_enabled("turn on the lights"))
        self.assertFalse(parse_fx_enabled("turn off effects"))
        self.assertFalse(resolve_fx_enabled("turn off effects"))
        self.assertFalse(resolve_fx_enabled("jarvis turn the effects off"))
        self.assertFalse(resolve_fx_enabled("disable effects"))
        self.assertFalse(resolve_fx_enabled("stop the glow"))
        self.assertFalse(resolve_fx_enabled("turn affects off"))
        self.assertFalse(resolve_fx_enabled("effects off please"))
        self.assertTrue(resolve_fx_enabled("turn effects on"))

    def test_delete_voice_phrases(self) -> None:
        from quest_assistant.parser import looks_like_delete_intent, normalize_voice_command

        action = parse_action("delete task 1", allow_implicit_add=False)
        self.assertEqual(action.kind, "delete")
        self.assertEqual(action.quest_number, 1)

        action = parse_action("delete task one", allow_implicit_add=False)
        self.assertEqual(action.kind, "delete")
        self.assertEqual(action.quest_number, 1)

        action = parse_action("can you delete task 1", allow_implicit_add=False)
        self.assertEqual(action.kind, "delete")
        self.assertEqual(action.quest_number, 1)

        action = parse_action("delete the quest washed dishes", allow_implicit_add=False)
        self.assertEqual(action.kind, "delete")
        self.assertEqual(action.title, "washed dishes")

        action = parse_action("remove quest three", allow_implicit_add=False)
        self.assertEqual(action.kind, "delete")
        self.assertEqual(action.quest_number, 3)

        self.assertTrue(looks_like_delete_intent("please delete task 2"))
        self.assertEqual(normalize_voice_command("jarvis delete task 1"), "delete task 1")

        from quest_assistant.parser import is_delete_title_placeholder

        self.assertTrue(is_delete_title_placeholder("the quest"))
        self.assertTrue(is_delete_title_placeholder("task"))
        self.assertFalse(is_delete_title_placeholder("washed dishes"))
        quest_action = parse_action("delete the quest", allow_implicit_add=False)
        self.assertEqual(quest_action.kind, "delete")
        self.assertTrue(is_delete_title_placeholder(quest_action.title))

        from quest_assistant.parser import extract_delete_quest_number

        self.assertEqual(extract_delete_quest_number("deleted task 4"), 4)
        self.assertEqual(extract_delete_quest_number("delete task number 4"), 4)
        self.assertEqual(extract_delete_quest_number("delete tasks 4"), 4)
        self.assertTrue(looks_like_delete_intent("deleted task 4"))

    def test_chat_detection(self) -> None:
        from quest_assistant.parser import local_chat_reply, looks_like_chat

        self.assertTrue(looks_like_chat("are you here"))
        self.assertTrue(looks_like_chat("Jarvis, are you here?"))
        self.assertTrue(looks_like_chat("how are you"))
        self.assertTrue(looks_like_chat("tell me a joke"))
        self.assertFalse(looks_like_chat("delete task 1"))
        self.assertFalse(looks_like_chat("add wash dishes"))
        self.assertEqual(local_chat_reply("are you here"), "Yes sir, I'm here.")
        self.assertEqual(parse_action("are you here", allow_implicit_add=True).kind, "noop")

    def test_edit_intent(self) -> None:
        action = parse_action("rename wash dished to wash dishes", allow_implicit_add=False)
        self.assertEqual(action.kind, "edit")
        self.assertEqual(action.title, "wash dished")
        self.assertEqual(action.value, "wash dishes")

        action = parse_action("edit task 2 to buy milk", allow_implicit_add=False)
        self.assertEqual(action.kind, "edit")
        self.assertEqual(action.quest_number, 2)
        self.assertEqual(action.value, "buy milk")

        action = parse_action("can you rename wash dished to wash dishes", allow_implicit_add=False)
        self.assertEqual(action.kind, "edit")
        self.assertEqual(action.title, "wash dished")
        self.assertEqual(action.value, "wash dishes")

        self.assertFalse(parse_action("change of plans", allow_implicit_add=False).kind == "edit")


class QuestDBTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db = QuestDB(Path(self._tmpdir.name) / "test.db")

    def tearDown(self) -> None:
        self.db.close()
        self._tmpdir.cleanup()

    def test_empty_title_rejected(self) -> None:
        self.assertIsNone(self.db.add_task("   "))

    def test_open_numbering_oldest_is_one(self) -> None:
        first = self.db.add_task("first")
        second = self.db.add_task("second")
        assert first is not None and second is not None
        one = self.db.get_open_task_by_number(1)
        two = self.db.get_open_task_by_number(2)
        assert one is not None and two is not None
        self.assertEqual(one.title, "first")
        self.assertEqual(two.title, "second")

    def test_like_wildcards_escaped(self) -> None:
        self.db.add_task("100% done")
        self.db.add_task("other")
        match = self.db.find_best_open_by_title("100%")
        self.assertIsNotNone(match)
        assert match is not None
        self.assertEqual(match.title, "100% done")

    def test_short_needle_rejected(self) -> None:
        self.db.add_task("abc")
        self.assertIsNone(self.db.find_best_open_by_title("a"))
        self.assertEqual(self.db.find_open_by_title_contains("a"), [])

    def test_invalid_status_rejected(self) -> None:
        task_id = self.db.add_task("x")
        assert task_id is not None
        with self.assertRaises(ValueError):
            self.db.set_status(task_id, "pending")

    def test_update_task_title(self) -> None:
        task_id = self.db.add_task("old title")
        assert task_id is not None
        self.assertTrue(self.db.update_task_title(task_id, "new title"))
        task = self.db.get_task(task_id)
        assert task is not None
        self.assertEqual(task.title, "new title")
        self.assertFalse(self.db.update_task_title(task_id, "   "))


if __name__ == "__main__":
    unittest.main()
