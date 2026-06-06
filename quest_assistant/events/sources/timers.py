from __future__ import annotations

import re
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Callable, Optional

from quest_assistant.events.bus import AssistantEvent


@dataclass
class _TimerEntry:
    timer_id: str
    label: str
    fire_at: float


class TimerScheduler:
    """In-process timers; posts to the event bus when they fire."""

    def __init__(self, post: Callable[[AssistantEvent], None]) -> None:
        self._post = post
        self._lock = threading.Lock()
        self._timers: dict[str, _TimerEntry] = {}
        self._stop = threading.Event()

    def start(self) -> None:
        threading.Thread(target=self._loop, name="TimerScheduler", daemon=True).start()

    def stop(self) -> None:
        self._stop.set()

    def schedule(self, label: str, delay_s: float) -> str:
        timer_id = uuid.uuid4().hex[:8]
        entry = _TimerEntry(timer_id=timer_id, label=label, fire_at=time.monotonic() + max(1.0, delay_s))
        with self._lock:
            self._timers[timer_id] = entry
        return timer_id

    def _loop(self) -> None:
        while not self._stop.wait(0.5):
            now = time.monotonic()
            due: list[_TimerEntry] = []
            with self._lock:
                remaining: dict[str, _TimerEntry] = {}
                for tid, entry in self._timers.items():
                    if entry.fire_at <= now:
                        due.append(entry)
                    else:
                        remaining[tid] = entry
                self._timers = remaining
            for entry in due:
                self._post(
                    AssistantEvent(
                        kind="timer",
                        message=f"Timer done: {entry.label}, sir.",
                        detail={"timer_id": entry.timer_id, "label": entry.label},
                    )
                )


_DURATION_RE = re.compile(
    r"\b(?:in|for)\s+(\d+)\s*(seconds?|secs?|minutes?|mins?|hours?|hrs?)\b",
    re.IGNORECASE,
)
_TIMER_INTENT_RE = re.compile(
    r"\b(?:set\s+(?:a\s+)?timer|remind\s+me|timer)\b",
    re.IGNORECASE,
)


def parse_timer_request(text: str) -> Optional[tuple[str, float]]:
    """Return (label, delay_seconds) for 'timer in 5 minutes' style phrases."""
    if not _TIMER_INTENT_RE.search(text):
        return None
    match = _DURATION_RE.search(text)
    if not match:
        return None
    amount = int(match.group(1))
    unit = match.group(2).lower()
    if unit.startswith("sec"):
        delay = float(amount)
    elif unit.startswith("min"):
        delay = float(amount * 60)
    else:
        delay = float(amount * 3600)
    label = re.sub(r"\s+", " ", text).strip()[:80] or "Reminder"
    return label, delay
