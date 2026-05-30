from __future__ import annotations

import traceback
from datetime import datetime
from pathlib import Path


def log_diagnostic(kind: str, message: str, *, exc: BaseException | None = None) -> None:
    """Append a line to ~/.quest_assistant/diagnostic.log (never raises)."""
    try:
        log_path = Path.home() / ".quest_assistant" / "diagnostic.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().isoformat(timespec="seconds")
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(f"[{stamp}] {kind}: {message}\n")
            if exc is not None:
                traceback.print_exception(type(exc), exc, exc.__traceback__, file=handle)
    except Exception:
        pass
