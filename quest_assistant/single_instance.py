from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QLockFile


def acquire_single_instance_lock() -> QLockFile | None:
    """Return a held lock, or None if another Assistance instance is running."""
    lock_dir = Path.home() / ".quest_assistant"
    lock_dir.mkdir(parents=True, exist_ok=True)
    lock_path = lock_dir / "assistance.lock"

    lock = QLockFile(str(lock_path))
    lock.setStaleLockTime(15_000)
    if lock.tryLock(250):
        return lock

    if lock.error() == QLockFile.LockError.LockFailedError:
        return None

    # Recover from a crashed prior run leaving a stale lock file.
    lock.removeStaleLockFile()
    if lock.tryLock(250):
        return lock
    return None
