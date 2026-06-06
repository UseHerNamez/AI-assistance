from __future__ import annotations

import threading
import time
import traceback
from datetime import datetime
from pathlib import Path
from typing import Optional

_LOG_DIR: Optional[Path] = None
_LOG_IO_LOCK = threading.Lock()


def log_dir() -> Path:
    global _LOG_DIR
    if _LOG_DIR is None:
        _LOG_DIR = Path.home() / ".quest_assistant" / "logs"
        _LOG_DIR.mkdir(parents=True, exist_ok=True)
    return _LOG_DIR


def _append(channel: str, message: str, *, exc: BaseException | None = None) -> None:
    try:
        path = log_dir() / f"{channel}.log"
        stamp = datetime.now().isoformat(timespec="seconds")
        with _LOG_IO_LOCK:
            with path.open("a", encoding="utf-8") as handle:
                handle.write(f"[{stamp}] {message}\n")
                if exc is not None:
                    traceback.print_exception(type(exc), exc, exc.__traceback__, file=handle)
    except Exception:
        pass


def log_voice(message: str) -> None:
    _append("voice", message)


def log_intent(message: str) -> None:
    _append("intent", message)


def log_action(message: str) -> None:
    _append("actions", message)


def log_error(message: str, *, exc: BaseException | None = None) -> None:
    _append("errors", message, exc=exc)


def log_event(message: str) -> None:
    _append("events", message)


class TimedRoute:
    """Context manager for intent route timing."""

    def __init__(self, route_path: str, utterance: str) -> None:
        self.route_path = route_path
        self.utterance = utterance[:120]
        self._start = time.perf_counter()

    def finish(self, *, kind: str, tool_count: int = 0) -> None:
        elapsed_ms = (time.perf_counter() - self._start) * 1000.0
        log_intent(
            f"path={self.route_path} kind={kind} tools={tool_count} "
            f"ms={elapsed_ms:.0f} text={self.utterance!r}"
        )
