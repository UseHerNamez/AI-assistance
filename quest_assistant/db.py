from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable, Optional


def _app_dir() -> Path:
    d = Path.home() / ".quest_assistant"
    d.mkdir(parents=True, exist_ok=True)
    return d


def default_db_path() -> Path:
    return _app_dir() / "quests.db"


@dataclass(frozen=True)
class Task:
    id: int
    title: str
    status: str  # "open" | "done"
    created_at: str
    updated_at: str
    due_iso: Optional[str] = None
    tags: Optional[str] = None
    source: Optional[str] = None
    raw_input: Optional[str] = None


class QuestDB:
    def __init__(self, path: Path | str | None = None) -> None:
        self.path = Path(path) if path else default_db_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path)
        self._conn.row_factory = sqlite3.Row
        self._init_schema()

    def close(self) -> None:
        self._conn.close()

    def _init_schema(self) -> None:
        cur = self._conn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS tasks (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              title TEXT NOT NULL,
              status TEXT NOT NULL DEFAULT 'open',
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL,
              due_iso TEXT,
              tags TEXT,
              source TEXT,
              raw_input TEXT
            );
            """
        )
        cur.execute("CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_tasks_due ON tasks(due_iso);")
        self._conn.commit()

    def add_task(
        self,
        title: str,
        *,
        due_iso: Optional[str] = None,
        tags: Optional[str] = None,
        source: Optional[str] = None,
        raw_input: Optional[str] = None,
    ) -> int:
        now = datetime.utcnow().isoformat(timespec="seconds")
        cur = self._conn.cursor()
        cur.execute(
            """
            INSERT INTO tasks (title, status, created_at, updated_at, due_iso, tags, source, raw_input)
            VALUES (?, 'open', ?, ?, ?, ?, ?, ?)
            """,
            (title.strip(), now, now, due_iso, tags, source, raw_input),
        )
        self._conn.commit()
        return int(cur.lastrowid)

    def set_status(self, task_id: int, status: str) -> None:
        now = datetime.utcnow().isoformat(timespec="seconds")
        cur = self._conn.cursor()
        cur.execute(
            "UPDATE tasks SET status = ?, updated_at = ? WHERE id = ?",
            (status, now, task_id),
        )
        self._conn.commit()

    def delete_task(self, task_id: int) -> None:
        cur = self._conn.cursor()
        cur.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
        self._conn.commit()

    def list_tasks(self, *, status: Optional[str] = None) -> list[Task]:
        cur = self._conn.cursor()
        if status:
            rows = cur.execute(
                "SELECT * FROM tasks WHERE status = ? ORDER BY id DESC", (status,)
            ).fetchall()
        else:
            rows = cur.execute("SELECT * FROM tasks ORDER BY id DESC").fetchall()
        return [self._row_to_task(r) for r in rows]

    def find_open_by_title_contains(self, needle: str, limit: int = 10) -> list[Task]:
        cur = self._conn.cursor()
        rows = cur.execute(
            """
            SELECT * FROM tasks
            WHERE status = 'open' AND title LIKE ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (f"%{needle}%", limit),
        ).fetchall()
        return [self._row_to_task(r) for r in rows]

    def find_by_title_contains(self, needle: str, limit: int = 10) -> list[Task]:
        cur = self._conn.cursor()
        rows = cur.execute(
            """
            SELECT * FROM tasks
            WHERE title LIKE ?
            ORDER BY status = 'open' DESC, id DESC
            LIMIT ?
            """,
            (f"%{needle}%", limit),
        ).fetchall()
        return [self._row_to_task(r) for r in rows]

    @staticmethod
    def _row_to_task(r: sqlite3.Row) -> Task:
        return Task(
            id=int(r["id"]),
            title=str(r["title"]),
            status=str(r["status"]),
            created_at=str(r["created_at"]),
            updated_at=str(r["updated_at"]),
            due_iso=r["due_iso"],
            tags=r["tags"],
            source=r["source"],
            raw_input=r["raw_input"],
        )

