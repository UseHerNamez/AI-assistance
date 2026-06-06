"""Tests for LLM open-action coalescing."""

from __future__ import annotations

import unittest

from quest_assistant.intent import tools as T
from quest_assistant.intent.tools import llm_actions_to_tool_calls
from quest_assistant.local_llm import LLMAction


class OpenToolCoalesceTests(unittest.TestCase):
    def test_parser_open_browser_drops_llm_search(self) -> None:
        calls = llm_actions_to_tool_calls(
            [
                LLMAction(kind="open_browser", value="always_open_browser"),
                LLMAction(kind="web_search", value="always_open_browser"),
            ],
            utterance="open browser",
            allow_add=False,
            jarvis_awake=True,
            source="voice",
        )
        self.assertEqual([c.name for c in calls], [T.TOOL_OPEN_BROWSER])

    def test_parser_open_outlook_drops_llm_open_app(self) -> None:
        calls = llm_actions_to_tool_calls(
            [LLMAction(kind="open_app", value="Chrome"), LLMAction(kind="open_app", value="Outlook")],
            utterance="open outlook",
            allow_add=False,
            jarvis_awake=True,
            source="voice",
        )
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0].name, T.TOOL_OPEN_APP)
        self.assertEqual(calls[0].arguments.get("name"), "outlook")


if __name__ == "__main__":
    unittest.main()
