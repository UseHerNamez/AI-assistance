"""Tests for long-term memory store."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from quest_assistant.memory.ingest import try_ingest_rememberance
from quest_assistant.memory.store import MemoryStore


class MemoryStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "test.db"
        self.memory = MemoryStore(self.path)

    def tearDown(self) -> None:
        self.memory.close()
        self._tmp.cleanup()

    def test_prefs(self) -> None:
        self.memory.set_pref("browser_hint", "Firefox")
        self.assertEqual(self.memory.get_pref("browser_hint"), "Firefox")

    def test_facts(self) -> None:
        self.memory.set_fact("user", "Alex", key="name")
        facts = self.memory.list_facts()
        self.assertTrue(any(f.value == "Alex" for f in facts))

    def test_episodic_and_prompt(self) -> None:
        self.memory.add_episodic("last_add", "add: wash dishes")
        block = self.memory.format_for_llm()
        self.assertIn("wash dishes", block)

    def test_ingest_remember(self) -> None:
        self.assertTrue(try_ingest_rememberance(self.memory, "remember my browser is Firefox"))
        self.assertEqual(self.memory.get_pref("browser_hint"), "Firefox")

    def test_ingest_my_name(self) -> None:
        self.assertTrue(try_ingest_rememberance(self.memory, "my name is Alex"))
        facts = self.memory.list_facts()
        self.assertTrue(any(f.key == "name" and f.value == "Alex" for f in facts))

    def test_call_me_rejects_article_phrases(self) -> None:
        self.assertFalse(try_ingest_rememberance(self.memory, "call me a taxi to the airport"))
        self.assertEqual(self.memory.get_pref("user_address"), "sir")


if __name__ == "__main__":
    unittest.main()
