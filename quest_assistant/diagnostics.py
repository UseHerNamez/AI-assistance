from __future__ import annotations

import traceback
from datetime import datetime
from pathlib import Path

from quest_assistant.monitor.logger import _LOG_IO_LOCK


def log_diagnostic(kind: str, message: str, *, exc: BaseException | None = None) -> None:
    """Append a line to ~/.quest_assistant/diagnostic.log (never raises)."""
    try:
        log_path = Path.home() / ".quest_assistant" / "diagnostic.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().isoformat(timespec="seconds")
        with _LOG_IO_LOCK:
            with log_path.open("a", encoding="utf-8") as handle:
                handle.write(f"[{stamp}] {kind}: {message}\n")
                if exc is not None:
                    traceback.print_exception(type(exc), exc, exc.__traceback__, file=handle)
        try:
            from quest_assistant.monitor.logger import log_action, log_error, log_intent, log_voice

            channel = {
                "voice": log_voice,
                "llm": log_intent,
                "ui": log_action,
            }.get(kind, log_error)
            channel(f"{kind}: {message}")
            if exc is not None:
                log_error(f"{kind}: {message}", exc=exc)
        except Exception:
            pass
    except Exception:
        pass
