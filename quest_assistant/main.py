from __future__ import annotations

import sys

from PySide6 import QtCore, QtWidgets

from quest_assistant.db import QuestDB
from quest_assistant.single_instance import acquire_single_instance_lock
from quest_assistant.ui_widget import QuestWidget


def main() -> int:
    app = QtWidgets.QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)

    instance_lock = acquire_single_instance_lock()
    if instance_lock is None:
        return 0

    # Keep the lock alive for the lifetime of this process.
    app.aboutToQuit.connect(instance_lock.unlock)

    db = QuestDB()
    w = QuestWidget(db)

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
            w.speaker.stop()
        except Exception:
            pass
        try:
            w.sfx.stop()
        except Exception:
            pass
        db.close()

