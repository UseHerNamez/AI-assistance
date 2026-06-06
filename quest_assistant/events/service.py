from __future__ import annotations

import os

from quest_assistant.events.bus import EventBus
from quest_assistant.events.sources.battery import BatteryMonitor
from quest_assistant.events.sources.downloads import DownloadWatcher
from quest_assistant.events.sources.timers import TimerScheduler


def _env_flag(name: str, default: bool = True) -> bool:
    raw = os.environ.get(name, "1" if default else "0").strip().lower()
    return raw not in {"0", "false", "no", "off"}


class EventMonitorService:
    """Starts background monitors that post to the shared event bus."""

    def __init__(self, bus: EventBus) -> None:
        self.bus = bus
        self.timers = TimerScheduler(bus.post)
        self._monitors = []
        if _env_flag("JARVIS_EVENTS_BATTERY", True):
            self._monitors.append(BatteryMonitor(bus.post))
        if _env_flag("JARVIS_EVENTS_DOWNLOADS", True):
            self._monitors.append(DownloadWatcher(bus.post))
        self._monitors.append(self.timers)

    def start(self) -> None:
        self.timers.start()
        for monitor in self._monitors:
            if monitor is self.timers:
                continue
            monitor.start()

    def stop(self) -> None:
        self.timers.stop()
        for monitor in self._monitors:
            if hasattr(monitor, "stop"):
                monitor.stop()
