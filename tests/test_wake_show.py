"""Tests for wake/show voice commands."""

from __future__ import annotations

import unittest

from quest_assistant.intent import parser_route
from quest_assistant.intent import tools as T
from quest_assistant.parser import (
    parse_action,
    parse_background_sleep_intent,
    parse_open_target,
    parse_show_intent,
    resolve_show_command,
)
from quest_assistant.voice.listener import WakeWordGate


class ShowIntentTests(unittest.TestCase):
    def test_wake_up_phrases(self) -> None:
        for phrase in ("wake up", "wakeup", "hey wake up", "jarvis wake up"):
            self.assertTrue(parse_show_intent(phrase), msg=phrase)
            self.assertEqual(parse_action(phrase, allow_implicit_add=False).kind, "show")

    def test_open_up_is_show_not_app(self) -> None:
        self.assertTrue(parse_show_intent("open up"))
        self.assertTrue(parse_show_intent("openup"))
        self.assertIsNone(parse_open_target("open up"))
        calls = parser_route.route_parser_controls("open up")
        self.assertEqual([c.name for c in calls], [T.TOOL_SHOW])

    def test_bare_open_is_show(self) -> None:
        for phrase in ("open", "open.", "jarvis open", "open please", "open sir"):
            self.assertTrue(parse_show_intent(phrase), msg=phrase)
            self.assertEqual(parse_action(phrase, allow_implicit_add=False).kind, "show")
        self.assertIsNone(parse_open_target("open"))
        calls = parser_route.route_parser_controls("open")
        self.assertEqual([c.name for c in calls], [T.TOOL_SHOW])

    def test_wake_up_beats_effects_on(self) -> None:
        calls = parser_route.route_parser_controls("wake up and turn on effects")
        self.assertEqual([c.name for c in calls], [T.TOOL_SHOW])

    def test_open_youtube_still_works(self) -> None:
        self.assertFalse(parse_show_intent("open youtube"))
        target = parse_open_target("open youtube")
        self.assertIsNotNone(target)
        assert target is not None
        self.assertEqual(target[0], "url")

    def test_asr_summon_variants(self) -> None:
        cases = (
            "harvey's open up",
            "miss open up",
            "obvious wake up",
            "jarvis openness",
            "jarvis open up",
            "opened up",
        )
        for phrase in cases:
            self.assertTrue(parse_show_intent(phrase), msg=phrase)
            self.assertIsNotNone(resolve_show_command(phrase), msg=phrase)


class WakeWordGateTests(unittest.TestCase):
    def test_bare_jarvis_opens(self) -> None:
        gate = WakeWordGate(wake_word="jarvis")
        self.assertEqual(gate.feed("jarvis", require_wake_word=True), "wake up")

    def test_jarvis_wake_up_strips_wake_word(self) -> None:
        gate = WakeWordGate(wake_word="jarvis")
        self.assertEqual(gate.feed("jarvis wake up", require_wake_word=True), "wake up")

    def test_wake_up_without_jarvis_when_wake_required(self) -> None:
        gate = WakeWordGate(wake_word="jarvis")
        self.assertEqual(gate.feed("wake up", require_wake_word=True), "wake up")

    def test_jarvis_open_strips_to_show(self) -> None:
        gate = WakeWordGate(wake_word="jarvis")
        self.assertEqual(gate.feed("jarvis open", require_wake_word=True), "open")
        self.assertEqual(
            gate.feed("jarvis open.", require_wake_word=True, background_mode=True),
            "open",
        )

    def test_fuzzy_jarvis_alias(self) -> None:
        gate = WakeWordGate(wake_word="jarvis")
        self.assertEqual(gate.feed("jervis open youtube", require_wake_word=True), "open youtube")

    def test_armed_window_accepts_follow_up(self) -> None:
        gate = WakeWordGate(wake_word="jarvis", command_window_s=30.0)
        self.assertEqual(gate.feed("jarvis", require_wake_word=True), "wake up")
        self.assertEqual(gate.feed("open youtube", require_wake_word=True), "open youtube")

    def test_background_mode_allows_summon_without_jarvis(self) -> None:
        gate = WakeWordGate(wake_word="jarvis", command_window_s=30.0)
        cases = {
            "wake up": "wake up",
            "open up": "open up",
            "open": "open",
            "wakeup": "wakeup",
        }
        for spoken, expected in cases.items():
            self.assertEqual(
                gate.feed(spoken, require_wake_word=True, background_mode=True),
                expected,
                msg=spoken,
            )

    def test_background_mode_still_blocks_other_commands_without_jarvis(self) -> None:
        gate = WakeWordGate(wake_word="jarvis", command_window_s=30.0)
        self.assertIsNone(gate.feed("open youtube", require_wake_word=True, background_mode=True))
        self.assertIsNone(gate.feed("hey how are you doing", require_wake_word=True, background_mode=True))

    def test_background_mode_allows_follow_up_after_jarvis(self) -> None:
        gate = WakeWordGate(wake_word="jarvis", command_window_s=30.0)
        self.assertEqual(
            gate.feed("jarvis open youtube", require_wake_word=True, background_mode=True),
            "open youtube",
        )
        self.assertEqual(
            gate.feed("open reddit", require_wake_word=True, background_mode=True),
            "open reddit",
        )

    def test_background_mode_blocks_ambient_speech(self) -> None:
        gate = WakeWordGate(wake_word="jarvis", command_window_s=30.0)
        self.assertIsNone(gate.feed("open youtube", require_wake_word=True, background_mode=True))
        self.assertIsNone(gate.feed("hey how are you doing", require_wake_word=True, background_mode=True))

    def test_fuzzy_travis_alias(self) -> None:
        gate = WakeWordGate(wake_word="jarvis")
        self.assertEqual(gate.feed("travis open youtube", require_wake_word=True), "open youtube")

    def test_asr_open_up_variants(self) -> None:
        gate = WakeWordGate(wake_word="jarvis")
        for spoken in ("harvey's open up", "miss open up", "jarvis openness"):
            cmd = gate.feed(spoken, require_wake_word=True, background_mode=True)
            self.assertEqual(cmd, "open up", msg=spoken)


class BackgroundSleepTests(unittest.TestCase):
    def test_sleep_requires_wake_after_hide(self) -> None:
        for phrase in ("sleep", "go away", "jarvis sleep"):
            self.assertTrue(parse_background_sleep_intent(phrase), msg=phrase)

    def test_hide_does_not_deep_sleep(self) -> None:
        self.assertFalse(parse_background_sleep_intent("hide"))
        self.assertFalse(parse_background_sleep_intent("jarvis hide"))


if __name__ == "__main__":
    unittest.main()
