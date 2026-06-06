from __future__ import annotations

import sys
import threading
import time
from typing import Callable, Optional

from quest_assistant.events.bus import AssistantEvent


def _read_battery_status() -> tuple[Optional[int], bool]:
    """Return (percent, on_ac_power). on_ac_power is True when plugged in (AC online)."""
    if sys.platform != "win32":
        return None, False
    try:
        import ctypes

        class SYSTEM_POWER_STATUS(ctypes.Structure):
            _fields_ = [
                ("ACLineStatus", ctypes.c_byte),
                ("BatteryFlag", ctypes.c_byte),
                ("BatteryLifePercent", ctypes.c_byte),
                ("Reserved1", ctypes.c_byte),
                ("BatteryLifeTime", ctypes.c_ulong),
                ("BatteryFullLifeTime", ctypes.c_ulong),
            ]

        status = SYSTEM_POWER_STATUS()
        if not ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(status)):
            return None, False
        pct = int(status.BatteryLifePercent)
        if pct > 100:
            return None, False
        on_ac = int(status.ACLineStatus) == 1
        return pct, on_ac
    except Exception:
        return None, False


class BatteryMonitor:
    """Posts when battery crosses low/critical thresholds."""

    def __init__(
        self,
        post: Callable[[AssistantEvent], None],
        *,
        poll_s: float = 90.0,
        low_pct: int = 20,
        critical_pct: int = 10,
    ) -> None:
        self._post = post
        self._poll_s = poll_s
        self._low_pct = low_pct
        self._critical_pct = critical_pct
        self._stop = threading.Event()
        self._last_band: Optional[str] = None

    def start(self) -> None:
        threading.Thread(target=self._loop, name="BatteryMonitor", daemon=True).start()

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        while not self._stop.wait(self._poll_s):
            pct, on_ac = _read_battery_status()
            if pct is None:
                continue
            if on_ac:
                # Do not warn while charging; reset so unplugging can notify again.
                self._last_band = "charging"
                continue
            band = "ok"
            if pct <= self._critical_pct:
                band = "critical"
            elif pct <= self._low_pct:
                band = "low"
            if band == self._last_band:
                continue
            self._last_band = band
            if band == "ok":
                continue
            if band == "critical":
                self._post(
                    AssistantEvent(
                        kind="battery",
                        message=f"Battery critical at {pct} percent, sir.",
                        detail={"percent": pct, "band": band},
                    )
                )
            else:
                self._post(
                    AssistantEvent(
                        kind="battery",
                        message=f"Battery low at {pct} percent, sir.",
                        detail={"percent": pct, "band": band},
                    )
                )
