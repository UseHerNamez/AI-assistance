from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from quest_assistant.db import Task


def local_today_iso() -> str:
    return date.today().isoformat()


@dataclass(frozen=True)
class QuestListBuckets:
    open_daily: list[Task]
    open_normal: list[Task]
    done_daily: list[Task]
    done_normal: list[Task]

    @property
    def open_all(self) -> list[Task]:
        return [*self.open_daily, *self.open_normal]

    @property
    def total(self) -> int:
        return (
            len(self.open_daily)
            + len(self.open_normal)
            + len(self.done_daily)
            + len(self.done_normal)
        )
