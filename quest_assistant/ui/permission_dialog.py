from __future__ import annotations

from PySide6 import QtCore, QtWidgets

from quest_assistant.core.types import ToolCall
from quest_assistant.permissions.policy import describe_call, dialog_hint


class PermissionConfirmDialog(QtWidgets.QDialog):
    """Single confirmation for one or more medium/high tool calls."""

    def __init__(self, parent: QtWidgets.QWidget, calls: list[ToolCall]) -> None:
        super().__init__(parent)
        self.setWindowTitle("Confirm action")
        self.setModal(True)
        self.setMinimumWidth(380)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setSpacing(12)

        hint = dialog_hint(calls)
        if hint:
            hint_label = QtWidgets.QLabel(hint)
            hint_label.setWordWrap(True)
            hint_label.setStyleSheet("color: #a8b0c0; font-size: 12px;")
            layout.addWidget(hint_label)

        intro = QtWidgets.QLabel(
            "Jarvis wants to run the following:"
            if len(calls) > 1
            else "Jarvis wants to run:"
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        list_widget = QtWidgets.QListWidget()
        list_widget.setFocusPolicy(QtCore.Qt.FocusPolicy.NoFocus)
        for call in calls:
            list_widget.addItem(describe_call(call))
        list_widget.setFixedHeight(min(120, 28 * max(1, len(calls)) + 8))
        layout.addWidget(list_widget)

        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.StandardButton.Ok
            | QtWidgets.QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QtWidgets.QDialogButtonBox.StandardButton.Ok).setText("Allow")
        buttons.button(QtWidgets.QDialogButtonBox.StandardButton.Cancel).setText("Cancel")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
