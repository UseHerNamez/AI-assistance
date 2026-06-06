from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Callable, Set

from quest_assistant.events.bus import AssistantEvent

# Browser/app partial download extensions (not hidden dotfiles).
_PARTIAL_DOWNLOAD_SUFFIXES = (
    ".crdownload",
    ".part",
    ".tmp",
    ".download",
    ".partial",
)


def _is_finished_download_filename(name: str) -> bool:
    lower = name.lower()
    return not any(lower.endswith(suffix) for suffix in _PARTIAL_DOWNLOAD_SUFFIXES)


class DownloadWatcher:
    """Notify when a new file appears in the user's Downloads folder."""

    def __init__(
        self,
        post: Callable[[AssistantEvent], None],
        *,
        poll_s: float = 12.0,
        downloads_dir: Path | None = None,
    ) -> None:
        self._post = post
        self._poll_s = poll_s
        self._dir = downloads_dir or (Path.home() / "Downloads")
        self._known: Set[str] = set()
        self._stop = threading.Event()
        self._bootstrapped = False

    def start(self) -> None:
        threading.Thread(target=self._loop, name="DownloadWatcher", daemon=True).start()

    def stop(self) -> None:
        self._stop.set()

    def _snapshot(self) -> Set[str]:
        if not self._dir.is_dir():
            return set()
        names: Set[str] = set()
        try:
            for path in self._dir.iterdir():
                if path.is_file() and not path.name.startswith("."):
                    if _is_finished_download_filename(path.name):
                        names.add(path.name)
        except OSError:
            return set()
        return names

    def _loop(self) -> None:
        while not self._stop.wait(self._poll_s):
            current = self._snapshot()
            if not self._bootstrapped:
                self._known = current
                self._bootstrapped = True
                continue
            new_files = sorted(current - self._known)
            self._known = current
            for name in new_files[:3]:
                self._post(
                    AssistantEvent(
                        kind="download",
                        message=f"Download finished: {name}, sir.",
                        detail={"filename": name, "path": str(self._dir / name)},
                    )
                )
