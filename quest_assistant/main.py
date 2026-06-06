from __future__ import annotations

import os
import sys
import traceback
from pathlib import Path

from PySide6 import QtCore, QtWidgets

from quest_assistant.db import QuestDB
from quest_assistant.single_instance import (
    acquire_single_instance_lock,
    start_activation_server,
    try_activate_running_instance,
)
from quest_assistant.ui_widget import QuestWidget


def _install_crash_logger() -> None:
    log_path = Path.home() / ".quest_assistant" / "crash.log"

    def _write(kind: str, exc: BaseException) -> None:
        try:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            with log_path.open("a", encoding="utf-8") as handle:
                handle.write(f"\n--- {kind} ---\n")
                traceback.print_exception(type(exc), exc, exc.__traceback__, file=handle)
        except Exception:
            pass

    def _excepthook(exc_type, exc, tb) -> None:  # noqa: ANN001
        if exc is not None:
            _write("main", exc)
        sys.__excepthook__(exc_type, exc, tb)

    sys.excepthook = _excepthook

    if hasattr(QtCore, "qInstallMessageHandler"):
        def _qt_message_handler(mode, context, message) -> None:  # noqa: ANN001
            if mode in {QtCore.QtMsgType.QtFatalMsg, QtCore.QtMsgType.QtCriticalMsg}:
                try:
                    log_path.parent.mkdir(parents=True, exist_ok=True)
                    with log_path.open("a", encoding="utf-8") as handle:
                        handle.write(f"\n--- qt-{mode.name} ---\n{message}\n")
                except Exception:
                    pass

        QtCore.qInstallMessageHandler(_qt_message_handler)


def main() -> int:
    # Force local voice unless the user explicitly opted into Edge TTS.
    os.environ.setdefault("JARVIS_TTS_BACKEND", "sapi")
    _install_crash_logger()
    app = QtWidgets.QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)

    instance_lock = acquire_single_instance_lock()
    if instance_lock is None:
        try_activate_running_instance()
        return 0

    # Keep the lock alive for the lifetime of this process.
    app.aboutToQuit.connect(instance_lock.unlock)

    db = QuestDB()
    w = QuestWidget(db)
    _activation_server = start_activation_server(w.show_and_raise)

    # Start hidden: show only when commanded (e.g. "Jarvis wake up").
    w.hide()

    tray = QtWidgets.QSystemTrayIcon()
    tray.setToolTip("Assistance (Jarvis)")
    tray.setIcon(app.style().standardIcon(QtWidgets.QStyle.StandardPixmap.SP_ComputerIcon))

    menu = QtWidgets.QMenu()
    act_show = menu.addAction("Show")
    act_hide = menu.addAction("Hide")
    menu.addSeparator()
    act_quit = menu.addAction("Quit")

    act_show.triggered.connect(w.show_and_raise)
    act_hide.triggered.connect(w.hide)
    act_quit.triggered.connect(app.quit)

    tray.setContextMenu(menu)

    def on_tray_activated(reason: QtWidgets.QSystemTrayIcon.ActivationReason) -> None:
        if reason == QtWidgets.QSystemTrayIcon.ActivationReason.Trigger:
            if w.isVisible():
                w.hide()
            else:
                w.show_and_raise()

    tray.activated.connect(on_tray_activated)
    tray.show()

    try:
        return app.exec()
    finally:
        tray.hide()
        try:
            w.voice.stop()
        except Exception:
            pass
        try:
            w.speaker.shutdown()
        except Exception:
            pass
        try:
            w.sfx.stop()
        except Exception:
            pass
        db.close()
        try:
            w.memory.close()
        except Exception:
            pass
        try:
            w._event_service.stop()
        except Exception:
            pass

