"""Memory viewer routing and display formatting."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from quest_assistant.core.session import SessionContext
from quest_assistant.core.types import RouteKind
from quest_assistant.intent.router import IntentRouter
from quest_assistant.intent.tools import TOOL_HIDE_MEMORY, TOOL_SHOW_MEMORY
from quest_assistant.memory.detect import parse_hide_memory_intent, parse_show_memory_intent, resolve_memory_intent
from quest_assistant.memory.display import format_display_sections
from quest_assistant.memory.store import MemoryStore
from quest_assistant.parser import parse_hide_intent, parse_show_intent


class MemoryDetectTests(unittest.TestCase):
    def test_show_memory_phrases(self) -> None:
        for phrase in (
            "what do you remember",
            "show me your memory",
            "what were we working on",
            "Jarvis show memory",
            "open memory",
            "show me what you remember",
            "pull up your memory",
            "what did you save",
            "memory tab",
        ):
            self.assertEqual(resolve_memory_intent(phrase), "show", msg=phrase)

    def test_hide_memory_phrases(self) -> None:
        for phrase in (
            "hide memory",
            "close memory panel",
            "close the memory tab now",
            "close memory tab",
            "close memory",
            "jarvis close the memory",
            "shut the memory panel",
        ):
            self.assertEqual(resolve_memory_intent(phrase), "hide", msg=phrase)
        self.assertEqual(resolve_memory_intent("show memory"), "show")

    def test_asr_close_variants(self) -> None:
        for phrase in ("closed memory", "clothes memory", "close memories"):
            self.assertEqual(resolve_memory_intent(phrase), "hide", msg=phrase)

    def test_close_while_panel_open(self) -> None:
        for phrase in ("close", "close it", "close that", "close tab", "close please"):
            self.assertEqual(
                resolve_memory_intent(phrase, memory_panel_open=True),
                "hide",
                msg=phrase,
            )
        self.assertIsNone(resolve_memory_intent("close youtube", memory_panel_open=True))

    def test_memory_not_window_show_or_hide(self) -> None:
        self.assertFalse(parse_show_intent("show me what you remember"))
        self.assertFalse(parse_hide_intent("close memory"))
        self.assertTrue(parse_show_intent("show me"))


class MemoryDisplayTests(unittest.TestCase):
    def test_format_sections_include_prefs_and_facts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = MemoryStore(Path(tmp) / "mem.db")
            try:
                store.set_fact("user", "Alex", key="name")
                store.add_episodic("interaction", "added quest wash dishes")
                sections = format_display_sections(store)
                titles = [s.title for s in sections]
                self.assertIn("Preferences", titles)
                self.assertIn("Facts", titles)
                self.assertIn("Recent activity", titles)
                facts = next(s for s in sections if s.title == "Facts")
                self.assertTrue(any("Alex" in b for b in facts.bullets))
            finally:
                store.close()


class MemoryRouterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.router = IntentRouter()
        self.ctx = SessionContext(source="voice", jarvis_awake=True, may_use_ollama=False)

    def test_router_show_memory(self) -> None:
        decision = self.router.route("show me your memory", self.ctx)
        self.assertEqual(decision.kind, RouteKind.EXECUTE)
        self.assertEqual(decision.tool_calls[0].name, TOOL_SHOW_MEMORY)

    def test_router_hide_memory(self) -> None:
        decision = self.router.route("hide memory", self.ctx)
        self.assertEqual(decision.tool_calls[0].name, TOOL_HIDE_MEMORY)

    def test_router_close_memory_tab(self) -> None:
        decision = self.router.route("close the memory tab now", self.ctx)
        self.assertEqual(decision.kind, RouteKind.EXECUTE)
        self.assertEqual(decision.tool_calls[0].name, TOOL_HIDE_MEMORY)


if __name__ == "__main__":
    unittest.main()
