"""Tests for compose / draft voice commands."""

from __future__ import annotations

import unittest

from quest_assistant.compose.detect import parse_compose_request
from quest_assistant.core.session import SessionContext
from quest_assistant.core.types import RouteKind
from quest_assistant.intent.router import IntentRouter
from quest_assistant.system.compose import write_draft_file


class ComposeDetectTests(unittest.TestCase):
    def test_write_me_text_about(self) -> None:
        req = parse_compose_request("write me a text about solar energy")
        self.assertIsNotNone(req)
        assert req is not None
        self.assertEqual(req.destination, "notepad")
        self.assertIn("solar", req.topic.lower())

    def test_open_word_and_write(self) -> None:
        req = parse_compose_request("open word and write me an essay about dogs")
        self.assertIsNotNone(req)
        assert req is not None
        self.assertEqual(req.destination, "word")
        self.assertIn("dogs", req.topic.lower())

    def test_open_outlook_email(self) -> None:
        req = parse_compose_request("open outlook and write me an email about the meeting")
        self.assertIsNotNone(req)
        assert req is not None
        self.assertEqual(req.destination, "outlook")
        self.assertIn("meeting", req.topic.lower())

    def test_open_text_file(self) -> None:
        req = parse_compose_request("open text file and write about cats")
        self.assertIsNotNone(req)
        assert req is not None
        self.assertEqual(req.destination, "notepad")

    def test_open_word_renewable_energy(self) -> None:
        req = parse_compose_request(
            "open word and write me an essay about renewable energy just to test it"
        )
        self.assertIsNotNone(req)
        assert req is not None
        self.assertEqual(req.destination, "word")
        self.assertIn("renewable", req.topic.lower())

    def test_asr_write_me(self) -> None:
        req = parse_compose_request("open word and right me an essay about dogs")
        self.assertIsNotNone(req)
        assert req is not None
        self.assertEqual(req.destination, "word")

    def test_asr_open_ward(self) -> None:
        req = parse_compose_request("open ward and write me an essay about solar power")
        self.assertIsNotNone(req)
        assert req is not None
        self.assertEqual(req.destination, "word")

    def test_fallback_without_and(self) -> None:
        req = parse_compose_request("open word write me an essay about cats")
        self.assertIsNotNone(req)
        assert req is not None
        self.assertIn("cats", req.topic.lower())

    def test_open_word_create_text(self) -> None:
        req = parse_compose_request("open word and create a text about renewable energy")
        self.assertIsNotNone(req)
        assert req is not None
        self.assertEqual(req.destination, "word")
        self.assertIn("renewable", req.topic.lower())

    def test_not_add_quest(self) -> None:
        self.assertIsNone(parse_compose_request("add task wash dishes"))


class ComposeRouterTests(unittest.TestCase):
    def test_routes_to_compose_when_ollama(self) -> None:
        router = IntentRouter()
        ctx = SessionContext(source="voice", jarvis_awake=True, may_use_ollama=True)
        decision = router.route("write me a text about volcanoes", ctx)
        self.assertEqual(decision.kind, RouteKind.COMPOSE)
        self.assertEqual(decision.compose_request.destination, "notepad")

    def test_needs_ollama_hint(self) -> None:
        router = IntentRouter()
        ctx = SessionContext(source="voice", jarvis_awake=True, may_use_ollama=False)
        decision = router.route("write me a text about volcanoes", ctx)
        self.assertEqual(decision.kind, RouteKind.TYPED_HINT)


class ComposeFileTests(unittest.TestCase):
    def test_write_draft_file(self) -> None:
        path = write_draft_file("Hello world", "Test topic", suffix="txt")
        self.assertTrue(path.exists())
        self.assertEqual(path.read_text(encoding="utf-8"), "Hello world")
        path.unlink(missing_ok=True)


class ComposeDeliveryTests(unittest.TestCase):
    def test_open_in_word_uses_winword_not_notepad_fallback(self) -> None:
        from unittest.mock import patch

        from quest_assistant.system.compose import open_in_word

        with (
            patch("quest_assistant.system.compose.write_draft_file") as write_mock,
            patch("quest_assistant.system.compose.open_document_with_default_app") as open_mock,
            patch("quest_assistant.system.compose._resolve_app_paths") as resolve_mock,
            patch("quest_assistant.system.compose._shell_execute_app") as shell_mock,
            patch("quest_assistant.system.compose._run_path") as run_mock,
        ):
            from pathlib import Path

            draft = Path("C:/Users/test/Documents/Jarvis/draft.rtf")
            write_mock.return_value = draft
            open_mock.return_value = True

            ok, spoken = open_in_word("Long essay body.", "renewable energy")

        self.assertTrue(ok)
        self.assertIn("Word", spoken)
        open_mock.assert_called_once_with(draft)
        write_mock.assert_called_once()
        resolve_mock.assert_not_called()
        shell_mock.assert_not_called()
        run_mock.assert_not_called()

    def test_open_in_word_does_not_open_txt_when_word_missing(self) -> None:
        from unittest.mock import patch

        from quest_assistant.system.compose import open_in_word

        with (
            patch("quest_assistant.system.compose.write_draft_file") as write_mock,
            patch("quest_assistant.system.compose.open_document_with_default_app", return_value=False),
            patch("quest_assistant.system.compose._resolve_app_paths", return_value=None),
            patch("quest_assistant.system.compose.find_app_path", return_value=None),
            patch("quest_assistant.system.compose._run_path") as run_mock,
        ):
            from pathlib import Path

            write_mock.return_value = Path("C:/Users/test/Documents/Jarvis/draft.rtf")
            ok, spoken = open_in_word("Body.", "topic")

        self.assertFalse(ok)
        self.assertIn("Word", spoken)
        run_mock.assert_not_called()


class ComposeUiTests(unittest.TestCase):
    def test_ui_widget_imports_compose_request(self) -> None:
        import quest_assistant.ui_widget as ui_widget

        from quest_assistant.compose.models import ComposeRequest as Expected

        self.assertIs(ui_widget.ComposeRequest, Expected)
        req = Expected(destination="word", topic="renewable energy")
        self.assertTrue(isinstance(req, ui_widget.ComposeRequest))


if __name__ == "__main__":
    unittest.main()
