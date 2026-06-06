from __future__ import annotations

import re
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from quest_assistant.db import default_db_path

_MAX_EPISODIC_ROWS = 80
_MAX_FACTS_FOR_PROMPT = 12


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(tzinfo=None).isoformat(timespec="seconds")


@dataclass(frozen=True)
class MemoryFact:
    category: str
    key: Optional[str]
    value: str
    updated_at: str


@dataclass(frozen=True)
class EpisodicEntry:
    kind: str
    summary: str
    detail: Optional[str]
    created_at: str


class MemoryStore:
    """
    SQLite memory alongside quests (same file by default).

    Tables: memory_prefs, memory_facts, memory_episodic
    """

    def __init__(self, path: Path | str | None = None) -> None:
        self.path = Path(path) if path else default_db_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._init_schema()
            self._seed_defaults()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def _init_schema(self) -> None:
        cur = self._conn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS memory_prefs (
              key TEXT PRIMARY KEY,
              value TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS memory_facts (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              category TEXT NOT NULL DEFAULT 'general',
              fact_key TEXT,
              value TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );
            """
        )
        cur.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_memory_facts_cat_key
            ON memory_facts(category, fact_key);
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS memory_episodic (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              kind TEXT NOT NULL,
              summary TEXT NOT NULL,
              detail TEXT,
              created_at TEXT NOT NULL
            );
            """
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_memory_episodic_kind ON memory_episodic(kind, created_at DESC);"
        )
        self._conn.commit()

    def _seed_defaults(self) -> None:
        defaults = {
            "search_engine": "google",
            "browser_hint": "system default",
            "user_address": "sir",
        }
        now = _utc_now()
        for key, value in defaults.items():
            self._conn.execute(
                """
                INSERT OR IGNORE INTO memory_prefs (key, value, updated_at)
                VALUES (?, ?, ?)
                """,
                (key, value, now),
            )
        self._conn.commit()

    def set_pref(self, key: str, value: str) -> None:
        cleaned_key = (key or "").strip().lower()
        cleaned_value = (value or "").strip()
        if not cleaned_key or not cleaned_value:
            return
        now = _utc_now()
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO memory_prefs (key, value, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
                """,
                (cleaned_key, cleaned_value, now),
            )
            self._conn.commit()

    def get_pref(self, key: str, default: Optional[str] = None) -> Optional[str]:
        cleaned_key = (key or "").strip().lower()
        if not cleaned_key:
            return default
        with self._lock:
            row = self._conn.execute(
                "SELECT value FROM memory_prefs WHERE key = ?",
                (cleaned_key,),
            ).fetchone()
        return str(row["value"]) if row else default

    def list_prefs(self) -> dict[str, str]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT key, value FROM memory_prefs ORDER BY key ASC"
            ).fetchall()
        return {str(r["key"]): str(r["value"]) for r in rows}

    def set_fact(self, category: str, value: str, *, key: Optional[str] = None) -> None:
        cat = (category or "general").strip().lower() or "general"
        fact_key = (key or "").strip().lower() or ""
        cleaned_value = (value or "").strip()
        if not cleaned_value:
            return
        now = _utc_now()
        with self._lock:
            existing = self._conn.execute(
                """
                SELECT id FROM memory_facts
                WHERE category = ? AND fact_key = ?
                """,
                (cat, fact_key),
            ).fetchone()
            if existing:
                self._conn.execute(
                    """
                    UPDATE memory_facts SET value = ?, updated_at = ? WHERE id = ?
                    """,
                    (cleaned_value, now, int(existing["id"])),
                )
            else:
                self._conn.execute(
                    """
                    INSERT INTO memory_facts (category, fact_key, value, updated_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    (cat, fact_key, cleaned_value, now),
                )
            self._conn.commit()

    def list_facts(self, *, limit: int = _MAX_FACTS_FOR_PROMPT) -> list[MemoryFact]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT category, fact_key, value, updated_at
                FROM memory_facts
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                (max(1, limit),),
            ).fetchall()
        return [
            MemoryFact(
                category=str(r["category"]),
                key=str(r["fact_key"]) if r["fact_key"] else None,
                value=str(r["value"]),
                updated_at=str(r["updated_at"]),
            )
            for r in rows
        ]

    def add_episodic(
        self,
        kind: str,
        summary: str,
        *,
        detail: Optional[str] = None,
    ) -> None:
        k = (kind or "note").strip().lower()[:48]
        s = (summary or "").strip()[:280]
        if not k or not s:
            return
        d = (detail or "").strip()[:500] or None
        now = _utc_now()
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO memory_episodic (kind, summary, detail, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (k, s, d, now),
            )
            self._prune_episodic_locked()
            self._conn.commit()

    def _prune_episodic_locked(self) -> None:
        count = self._conn.execute("SELECT COUNT(*) AS c FROM memory_episodic").fetchone()
        excess = int(count["c"]) - _MAX_EPISODIC_ROWS
        if excess <= 0:
            return
        self._conn.execute(
            """
            DELETE FROM memory_episodic
            WHERE id IN (
              SELECT id FROM memory_episodic
              ORDER BY created_at ASC
              LIMIT ?
            )
            """,
            (excess,),
        )

    def recent_episodic(
        self,
        *,
        limit: int = 6,
        kinds: Optional[tuple[str, ...]] = None,
    ) -> list[EpisodicEntry]:
        with self._lock:
            if kinds:
                placeholders = ",".join("?" for _ in kinds)
                rows = self._conn.execute(
                    f"""
                    SELECT kind, summary, detail, created_at
                    FROM memory_episodic
                    WHERE kind IN ({placeholders})
                    ORDER BY created_at DESC
                    LIMIT ?
                    """,
                    (*kinds, max(1, limit)),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    """
                    SELECT kind, summary, detail, created_at
                    FROM memory_episodic
                    ORDER BY created_at DESC
                    LIMIT ?
                    """,
                    (max(1, limit),),
                ).fetchall()
        return [
            EpisodicEntry(
                kind=str(r["kind"]),
                summary=str(r["summary"]),
                detail=str(r["detail"]) if r["detail"] else None,
                created_at=str(r["created_at"]),
            )
            for r in rows
        ]

    def format_for_llm(self, *, max_chars: int = 900) -> str:
        """Compact block for LLM system/user context (not shown to parser)."""
        parts: list[str] = []

        prefs = self.list_prefs()
        if prefs:
            pref_lines = [f"{k}={v}" for k, v in sorted(prefs.items())[:8]]
            parts.append("Prefs: " + "; ".join(pref_lines))

        facts = self.list_facts(limit=_MAX_FACTS_FOR_PROMPT)
        if facts:
            fact_lines = []
            for f in facts:
                label = f.category if not f.key else f"{f.category}/{f.key}"
                fact_lines.append(f"{label}={f.value}")
            parts.append("Facts: " + "; ".join(fact_lines))

        episodic = self.recent_episodic(limit=5)
        if episodic:
            ep_lines = [f"{e.kind}: {e.summary}" for e in episodic]
            parts.append("Recent: " + " | ".join(ep_lines))

        if not parts:
            return ""

        block = "Known context about the user:\n" + "\n".join(parts)
        if len(block) > max_chars:
            block = block[: max_chars - 3] + "..."
        return block

    def record_interaction(self, summary: str, *, detail: Optional[str] = None) -> None:
        self.add_episodic("interaction", summary, detail=detail)

    def record_quest_event(self, event: str, *, title: str = "", number: Optional[int] = None) -> None:
        label = (event or "").strip().lower()
        if number is not None:
            summary = f"{label} task {number}"
            if title:
                summary += f" ({title})"
        else:
            summary = f"{label}: {title}" if title else label
        self.add_episodic(f"last_{label}", summary[:280], detail=title or None)
