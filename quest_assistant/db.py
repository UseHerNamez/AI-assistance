from __future__ import annotations

import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from quest_assistant.daily_tasks import QuestListBuckets, local_today_iso

VALID_STATUSES = frozenset({"open", "done"})
_MIN_TITLE_NEEDLE_LEN = 2


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(tzinfo=None).isoformat(timespec="seconds")


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
    is_daily: bool = False
    daily_done_on: Optional[str] = None


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
        self._migrate_schema(cur)
        self._conn.commit()

    def _migrate_schema(self, cur: sqlite3.Cursor) -> None:
        cols = {row[1] for row in cur.execute("PRAGMA table_info(tasks)").fetchall()}
        if "is_daily" not in cols:
            cur.execute("ALTER TABLE tasks ADD COLUMN is_daily INTEGER NOT NULL DEFAULT 0")
        if "daily_done_on" not in cols:
            cur.execute("ALTER TABLE tasks ADD COLUMN daily_done_on TEXT")

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
        now = _utc_now_iso()
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

    def add_daily_task(
        self,
        title: str,
        *,
        source: Optional[str] = None,
        raw_input: Optional[str] = None,
    ) -> Optional[int]:
        cleaned = (title or "").strip()
        if not cleaned:
            return None
        now = _utc_now_iso()
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                """
                INSERT INTO tasks (
                  title, status, created_at, updated_at, source, raw_input, is_daily
                )
                VALUES (?, 'open', ?, ?, ?, ?, 1)
                """,
                (cleaned, now, now, source, raw_input),
            )
            self._conn.commit()
            return int(cur.lastrowid)

    def rollover_daily_tasks(self) -> None:
        today = local_today_iso()
        now = _utc_now_iso()
        with self._lock:
            self._conn.execute(
                """
                UPDATE tasks
                SET status = 'open', daily_done_on = NULL, updated_at = ?
                WHERE is_daily = 1
                  AND status = 'done'
                  AND (daily_done_on IS NULL OR daily_done_on < ?)
                """,
                (now, today),
            )
            self._conn.commit()

    def complete_task(self, task_id: int) -> None:
        task = self.get_task(task_id)
        if task is None:
            return
        now = _utc_now_iso()
        with self._lock:
            if task.is_daily:
                self._conn.execute(
                    """
                    UPDATE tasks
                    SET status = 'done', daily_done_on = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (local_today_iso(), now, task_id),
                )
            else:
                self._conn.execute(
                    "UPDATE tasks SET status = 'done', updated_at = ? WHERE id = ?",
                    (now, task_id),
                )
            self._conn.commit()

    def can_delete_task(self, task_id: int) -> bool:
        task = self.get_task(task_id)
        if task is None:
            return False
        if task.is_daily and task.status == "done":
            return False
        return True

    def set_status(self, task_id: int, status: str) -> None:
        if status not in VALID_STATUSES:
            raise ValueError(f"invalid task status: {status!r}")
        if status == "done":
            self.complete_task(task_id)
            return
        now = _utc_now_iso()
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                "UPDATE tasks SET status = ?, updated_at = ? WHERE id = ?",
                (status, now, task_id),
            )
            self._conn.commit()

    def delete_task(self, task_id: int) -> bool:
        if not self.can_delete_task(task_id):
            return False
        with self._lock:
            cur = self._conn.cursor()
            cur.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
            self._conn.commit()
            return cur.rowcount > 0

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
        now = _utc_now_iso()
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                "UPDATE tasks SET title = ?, updated_at = ? WHERE id = ?",
                (cleaned, now, task_id),
            )
            self._conn.commit()
            return cur.rowcount > 0

    def list_open_tasks_display(self) -> list[Task]:
        buckets = self.list_quest_buckets()
        return buckets.open_all

    def list_quest_buckets(self) -> QuestListBuckets:
        self.rollover_daily_tasks()
        today = local_today_iso()
        with self._lock:
            cur = self._conn.cursor()
            open_daily = [
                self._row_to_task(r)
                for r in cur.execute(
                    """
                    SELECT * FROM tasks
                    WHERE status = 'open' AND is_daily = 1
                    ORDER BY id ASC
                    """
                ).fetchall()
            ]
            open_normal = [
                self._row_to_task(r)
                for r in cur.execute(
                    """
                    SELECT * FROM tasks
                    WHERE status = 'open' AND is_daily = 0
                    ORDER BY id ASC
                    """
                ).fetchall()
            ]
            done_daily = [
                self._row_to_task(r)
                for r in cur.execute(
                    """
                    SELECT * FROM tasks
                    WHERE status = 'done' AND is_daily = 1 AND daily_done_on = ?
                    ORDER BY id ASC
                    """,
                    (today,),
                ).fetchall()
            ]
            done_normal = [
                self._row_to_task(r)
                for r in cur.execute(
                    """
                    SELECT * FROM tasks
                    WHERE status = 'done' AND is_daily = 0
                    ORDER BY id DESC
                    """
                ).fetchall()
            ]
        return QuestListBuckets(
            open_daily=open_daily,
            open_normal=open_normal,
            done_daily=done_daily,
            done_normal=done_normal,
        )

    def list_tasks(self, *, status: Optional[str] = None) -> list[Task]:
        if status == "open":
            return self.list_open_tasks_display()
        if status == "done":
            buckets = self.list_quest_buckets()
            return [*buckets.done_daily, *buckets.done_normal]
        with self._lock:
            cur = self._conn.cursor()
            rows = cur.execute("SELECT * FROM tasks ORDER BY id DESC").fetchall()
        return [self._row_to_task(r) for r in rows]

    def get_open_task_by_number(self, number: int, *, daily: Optional[bool] = None) -> Optional[Task]:
        if number < 1:
            return None
        buckets = self.list_quest_buckets()
        if daily is True:
            pool = buckets.open_daily
        elif daily is False:
            pool = buckets.open_normal
        elif 1 <= number <= len(buckets.open_normal):
            return buckets.open_normal[number - 1]
        else:
            pool = buckets.open_daily
        if number > len(pool):
            return None
        return pool[number - 1]

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
        keys = r.keys()
        is_daily = bool(r["is_daily"]) if "is_daily" in keys else False
        daily_done_on = r["daily_done_on"] if "daily_done_on" in keys else None
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
            is_daily=is_daily,
            daily_done_on=daily_done_on,
        )
