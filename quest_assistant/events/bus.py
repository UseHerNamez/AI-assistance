from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from PySide6 import QtCore

from quest_assistant.monitor.logger import log_event


@dataclass(frozen=True)
class AssistantEvent:
    """Proactive or background notification (same pipeline as voice on the UI)."""

    kind: str
    message: str
    detail: dict[str, Any] = field(default_factory=dict)
    speak: bool = True


class EventBus(QtCore.QObject):
    """Thread-safe fan-in: background monitors and voice both post here."""

    event_posted = QtCore.Signal(object)

    def post(self, event: AssistantEvent) -> None:
        log_event(f"{event.kind}: {event.message[:120]}")
        self.event_posted.emit(event)
