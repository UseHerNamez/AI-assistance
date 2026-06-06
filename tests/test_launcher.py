"""Tests for open/search/download command parsing and URL resolution."""

from __future__ import annotations

import sys
import unittest

from quest_assistant.parser import (
    parse_action,
    parse_download_search_query,
    parse_open_target,
    parse_web_search_query,
)
from quest_assistant.system.launcher import (
    build_search_url,
    canonical_open_target,
    classify_open_target,
    resolve_site_url,
)


class OpenTargetParserTests(unittest.TestCase):
    def test_open_youtube_is_url(self) -> None:
        parsed = parse_open_target("open youtube")
        self.assertIsNotNone(parsed)
        assert parsed is not None
        self.assertEqual(parsed, ("url", "youtube"))
        self.assertEqual(parse_action("open youtube", allow_implicit_add=False).kind, "open_url")

    def test_open_you_tube_asr_variant(self) -> None:
        parsed = parse_open_target("open you tube")
        self.assertIsNotNone(parsed)
        assert parsed is not None
        self.assertEqual(parsed, ("url", "youtube"))

    def test_open_in_browser(self) -> None:
        from quest_assistant.parser import parse_open_browser_intent

        self.assertTrue(parse_open_browser_intent("open in browser"))
        self.assertTrue(parse_open_browser_intent("open browser"))
        self.assertEqual(parse_action("open in browser", allow_implicit_add=False).kind, "open_browser")

    def test_open_outlook_is_app(self) -> None:
        parsed = parse_open_target("open outlook")
        self.assertIsNotNone(parsed)
        assert parsed is not None
        self.assertEqual(parsed, ("app", "outlook"))
        self.assertEqual(parse_action("launch outlook", allow_implicit_add=False).kind, "open_app")

    def test_open_league_of_legends(self) -> None:
        parsed = parse_open_target("open league of legends")
        self.assertIsNotNone(parsed)
        assert parsed is not None
        self.assertEqual(parsed[0], "app")
        self.assertIn("league", parsed[1].lower())

    def test_browser_not_open_target(self) -> None:
        self.assertIsNone(parse_open_target("open the browser"))

    def test_voice_prefix_stripped(self) -> None:
        parsed = parse_open_target("jarvis open youtube")
        self.assertIsNotNone(parsed)
        assert parsed is not None
        self.assertEqual(parsed[1], "youtube")


class SearchParserTests(unittest.TestCase):
    def test_search_for_phrase(self) -> None:
        self.assertEqual(parse_web_search_query("search for python tutorials"), "python tutorials")
        self.assertEqual(parse_web_search_query("find me best laptops"), "best laptops")
        self.assertEqual(parse_action("look for cat videos", allow_implicit_add=False).kind, "web_search")

    def test_download_query(self) -> None:
        self.assertEqual(parse_download_search_query("download vlc"), "vlc")
        self.assertEqual(parse_download_search_query("get me python 3.12"), "python 3.12")
        self.assertEqual(parse_action("download obs studio", allow_implicit_add=False).kind, "download_search")


class LauncherUrlTests(unittest.TestCase):
    def test_known_site(self) -> None:
        self.assertEqual(resolve_site_url("youtube"), "https://www.youtube.com")
        self.assertEqual(classify_open_target("youtube"), "url")

    def test_canonical_you_tube(self) -> None:
        self.assertEqual(canonical_open_target("you tube"), "youtube")
        self.assertEqual(classify_open_target("you tube"), "url")

    def test_canonical_outlook_asr(self) -> None:
        self.assertEqual(canonical_open_target("all look"), "outlook")
        self.assertEqual(canonical_open_target("out look"), "outlook")
        parsed = parse_open_target("open all look")
        self.assertIsNotNone(parsed)
        assert parsed is not None
        self.assertEqual(parsed, ("app", "outlook"))

    def test_browser_destination_sanitizer(self) -> None:
        from quest_assistant.system.launcher import is_valid_browser_destination, resolve_browser_destination

        self.assertFalse(is_valid_browser_destination("always_open_browser"))
        url, spoken = resolve_browser_destination("always_open_browser")
        self.assertEqual(url, "https://www.google.com")
        self.assertEqual(spoken, "Opening browser.")

    def test_domain_like(self) -> None:
        self.assertEqual(resolve_site_url("github.com"), "https://github.com")

    def test_download_search_url(self) -> None:
        url = build_search_url("vlc", download=True)
        self.assertIn("vlc", url)
        self.assertIn("download", url)

    @unittest.skipUnless(sys.platform == "win32", "Windows-only")
    def test_outlook_prefers_new_outlook_when_installed(self) -> None:
        from pathlib import Path

        from quest_assistant.system.launcher import find_app_path

        olk = Path.home() / "AppData/Local/Microsoft/WindowsApps/olk.exe"
        path = find_app_path("outlook")
        if path is None:
            self.skipTest("Outlook not installed")
        if olk.exists():
            self.assertEqual(path, str(olk))
        else:
            self.assertTrue(path.lower().endswith((".lnk", ".exe")))


    @unittest.skipUnless(sys.platform == "win32", "Windows-only")
    def test_find_word_prefers_exe_over_lnk(self) -> None:
        from quest_assistant.system.launcher import find_app_path

        path = find_app_path("word")
        if path is None:
            self.skipTest("Word not installed")
        self.assertTrue(path.lower().endswith(".exe"), path)

    @unittest.skipUnless(sys.platform == "win32", "Windows-only")
    def test_launch_word_opens_blank_document(self) -> None:
        from unittest.mock import patch

        from quest_assistant.system.launcher import launch_app

        with (
            patch("quest_assistant.system.launcher._office_blank_starter") as starter_mock,
            patch("quest_assistant.system.launcher.open_document_with_default_app") as open_mock,
            patch("quest_assistant.system.launcher._launch_gui_path") as launch_mock,
        ):
            from pathlib import Path

            starter = Path(r"C:\Users\test\Documents\Jarvis\new_document_20260101_120000.rtf")
            starter_mock.return_value = starter
            open_mock.return_value = True
            ok, spoken = launch_app("word", launch=True)

        self.assertTrue(ok)
        self.assertIn("word", spoken.lower())
        open_mock.assert_called_once_with(starter)
        launch_mock.assert_not_called()


if __name__ == "__main__":
    unittest.main()
