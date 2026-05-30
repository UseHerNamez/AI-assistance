from __future__ import annotations

import os
import queue
import threading
from typing import Optional

try:
    import winsound
except Exception:  # pragma: no cover - Windows-only
    winsound = None


class SoundEffects:
    """Tiny async sound-effect engine using local Windows beeps."""

    _PATTERNS: dict[str, tuple[tuple[int, int], ...]] = {
        "show": ((740, 45), (988, 70), (1319, 90)),
        "hide": ((988, 55), (740, 70), (494, 90)),
        "mute": ((392, 90), (262, 130)),
        "unmute": ((523, 50), (784, 80), (1047, 90)),
        "add": ((659, 45), (880, 70)),
        "complete": ((784, 45), (1047, 55), (1319, 85)),
        "delete": ((440, 55), (330, 75)),
        "error": ((220, 120),),
    }

    def __init__(self) -> None:
        self.enabled = os.environ.get("JARVIS_SFX", "0").strip().lower() in {"1", "true", "on"}
        self._queue: "queue.Queue[Optional[str]]" = queue.Queue()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def play(self, name: str) -> None:
        if self.enabled and winsound is not None:
            self._queue.put(name)

    def stop(self) -> None:
        self._queue.put(None)

    def _run(self) -> None:
        while True:
            name = self._queue.get()
            if name is None:
                break
            pattern = self._PATTERNS.get(name)
            if not pattern:
                continue
            for frequency, duration_ms in pattern:
                try:
                    winsound.Beep(frequency, duration_ms)
                except Exception:
                    break

