from __future__ import annotations

import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

VALID_STATUSES = frozenset({"open", "done"})
_MIN_TITLE_NEEDLE_LEN = 2


def _app_dir() -> Path:
    d = Path.home() / ".quest_assistant"
    d.mkdir(parents=True, exist_ok=True)
    return d


def default_db_path() -> Path:
    return _app_dir() / "quests.db"


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


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
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._init_schema()

    def close(self) -> None:
        with self._lock:
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
    ) -> Optional[int]:
        cleaned = (title or "").strip()
        if not cleaned:
            return None
        now = datetime.utcnow().isoformat(timespec="seconds")
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                """
                INSERT INTO tasks (title, status, created_at, updated_at, due_iso, tags, source, raw_input)
                VALUES (?, 'open', ?, ?, ?, ?, ?, ?)
                """,
                (cleaned, now, now, due_iso, tags, source, raw_input),
            )
            self._conn.commit()
            return int(cur.lastrowid)

    def set_status(self, task_id: int, status: str) -> None:
        if status not in VALID_STATUSES:
            raise ValueError(f"invalid task status: {status!r}")
        now = datetime.utcnow().isoformat(timespec="seconds")
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                "UPDATE tasks SET status = ?, updated_at = ? WHERE id = ?",
                (status, now, task_id),
            )
            self._conn.commit()

    def delete_task(self, task_id: int) -> None:
        with self._lock:
            cur = self._conn.cursor()
            cur.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
            self._conn.commit()

    def get_task(self, task_id: int) -> Optional[Task]:
        with self._lock:
            row = self._conn.cursor().execute(
                "SELECT * FROM tasks WHERE id = ?",
                (task_id,),
            ).fetchone()
        return self._row_to_task(row) if row else None

    def update_task_title(self, task_id: int, title: str) -> bool:
        cleaned = (title or "").strip()
        if not cleaned:
            return False
        now = datetime.utcnow().isoformat(timespec="seconds")
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                "UPDATE tasks SET title = ?, updated_at = ? WHERE id = ?",
                (cleaned, now, task_id),
            )
            self._conn.commit()
            return cur.rowcount > 0

    def list_tasks(self, *, status: Optional[str] = None) -> list[Task]:
        with self._lock:
            cur = self._conn.cursor()
            if status == "open":
                rows = cur.execute(
                    "SELECT * FROM tasks WHERE status = ? ORDER BY id ASC", (status,)
                ).fetchall()
            elif status:
                rows = cur.execute(
                    "SELECT * FROM tasks WHERE status = ? ORDER BY id DESC", (status,)
                ).fetchall()
            else:
                rows = cur.execute("SELECT * FROM tasks ORDER BY id DESC").fetchall()
        return [self._row_to_task(r) for r in rows]

    def get_open_task_by_number(self, number: int) -> Optional[Task]:
        if number < 1:
            return None
        tasks = self.list_tasks(status="open")
        if number > len(tasks):
            return None
        return tasks[number - 1]

    def find_open_by_title_contains(self, needle: str, limit: int = 10) -> list[Task]:
        cleaned = (needle or "").strip()
        if len(cleaned) < _MIN_TITLE_NEEDLE_LEN:
            return []
        with self._lock:
            cur = self._conn.cursor()
            escaped = _escape_like(cleaned)
            rows = cur.execute(
                """
                SELECT * FROM tasks
                WHERE status = 'open' AND title LIKE ? ESCAPE '\\'
                ORDER BY id ASC
                LIMIT ?
                """,
                (f"%{escaped}%", limit),
            ).fetchall()
        return [self._row_to_task(r) for r in rows]

    def find_best_open_by_title(self, needle: str) -> Optional[Task]:
        cleaned = (needle or "").strip()
        if len(cleaned) < _MIN_TITLE_NEEDLE_LEN:
            return None
        with self._lock:
            cur = self._conn.cursor()
            row = cur.execute(
                """
                SELECT * FROM tasks
                WHERE status = 'open' AND lower(title) = lower(?)
                ORDER BY id ASC
                LIMIT 1
                """,
                (cleaned,),
            ).fetchone()
            if row:
                return self._row_to_task(row)
            escaped = _escape_like(cleaned)
            row = cur.execute(
                """
                SELECT * FROM tasks
                WHERE status = 'open' AND title LIKE ? ESCAPE '\\'
                ORDER BY id ASC
                LIMIT 1
                """,
                (f"%{escaped}%",),
            ).fetchone()
        return self._row_to_task(row) if row else None

    def find_by_title_contains(self, needle: str, limit: int = 10) -> list[Task]:
        cleaned = (needle or "").strip()
        if len(cleaned) < _MIN_TITLE_NEEDLE_LEN:
            return []
        with self._lock:
            cur = self._conn.cursor()
            escaped = _escape_like(cleaned)
            rows = cur.execute(
                """
                SELECT * FROM tasks
                WHERE title LIKE ? ESCAPE '\\'
                ORDER BY status = 'open' DESC, id ASC
                LIMIT ?
                """,
                (f"%{escaped}%", limit),
            ).fetchall()
        return [self._row_to_task(r) for r in rows]

    def find_best_by_title(self, needle: str) -> Optional[Task]:
        cleaned = (needle or "").strip()
        if len(cleaned) < _MIN_TITLE_NEEDLE_LEN:
            return None
        with self._lock:
            cur = self._conn.cursor()
            row = cur.execute(
                """
                SELECT * FROM tasks
                WHERE lower(title) = lower(?)
                ORDER BY status = 'open' DESC, id ASC
                LIMIT 1
                """,
                (cleaned,),
            ).fetchone()
            if row:
                return self._row_to_task(row)
            escaped = _escape_like(cleaned)
            row = cur.execute(
                """
                SELECT * FROM tasks
                WHERE title LIKE ? ESCAPE '\\'
                ORDER BY status = 'open' DESC, id ASC
                LIMIT 1
                """,
                (f"%{escaped}%",),
            ).fetchone()
        return self._row_to_task(row) if row else None

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
