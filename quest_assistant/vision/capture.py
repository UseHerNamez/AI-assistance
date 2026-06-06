from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Optional

from PySide6 import QtGui


def capture_primary_screen(path: Optional[Path] = None) -> Optional[Path]:
    """Grab the primary display to a PNG file."""
    screen = QtGui.QGuiApplication.primaryScreen()
    if screen is None:
        return None
    pixmap = screen.grabWindow(0)
    if pixmap.isNull():
        return None
    target = path or Path(tempfile.gettempdir()) / "jarvis_screen.png"
    if not pixmap.save(str(target), "PNG"):
        return None
    return target
