from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QLockFile
from PySide6.QtNetwork import QLocalServer, QLocalSocket

ACTIVATION_SERVER_NAME = "quest_assistant_activation"


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


def try_activate_running_instance() -> bool:
    """If another instance is running, ask it to show the window."""
    sock = QLocalSocket()
    sock.connectToServer(ACTIVATION_SERVER_NAME)
    if not sock.waitForConnected(500):
        return False
    sock.write(b"show")
    sock.waitForBytesWritten(500)
    sock.disconnectFromServer()
    return True


def start_activation_server(on_activate: Callable[[], None]) -> QLocalServer | None:
    """Listen for show requests from a second launch (e.g. desktop shortcut)."""
    QLocalServer.removeServer(ACTIVATION_SERVER_NAME)
    server = QLocalServer()
    if not server.listen(ACTIVATION_SERVER_NAME):
        return None

    def _on_new_connection() -> None:
        conn = server.nextPendingConnection()
        if conn is None:
            return

        def _on_ready_read() -> None:
            if conn.bytesAvailable() <= 0:
                return
            if conn.readAll().data() == b"show":
                on_activate()
            conn.disconnectFromServer()

        conn.readyRead.connect(_on_ready_read)

    server.newConnection.connect(_on_new_connection)
    return server
