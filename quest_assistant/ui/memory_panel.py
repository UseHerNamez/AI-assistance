from __future__ import annotations

from typing import Callable, Optional

from PySide6 import QtCore, QtWidgets

from quest_assistant.memory.display import MemoryDisplaySection, format_display_sections
from quest_assistant.memory.store import MemoryStore


class MemorySidePanel(QtWidgets.QFrame):
    """Slides in from the right of the quest card to show stored memory."""

    OPEN_WIDTH = 272

    def __init__(
        self,
        get_store: Callable[[], MemoryStore],
        *,
        on_open_changed: Optional[Callable[[bool], None]] = None,
        parent: Optional[QtWidgets.QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._get_store = get_store
        self._on_open_changed = on_open_changed
        self._open = False

        self.setObjectName("memoryPanel")
        self.setMaximumWidth(0)
        self.setMinimumWidth(0)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(12, 14, 14, 14)
        layout.setSpacing(8)

        header = QtWidgets.QHBoxLayout()
        title = QtWidgets.QLabel("Memory")
        title.setStyleSheet("font-size: 14px; font-weight: 700;")
        header.addWidget(title)
        header.addStretch(1)
        close_btn = QtWidgets.QPushButton("×")
        close_btn.setFixedSize(28, 28)
        close_btn.setToolTip("Close memory panel")
        close_btn.clicked.connect(lambda: self.set_open(False))
        header.addWidget(close_btn)
        layout.addLayout(header)

        hint = QtWidgets.QLabel("What Jarvis keeps for you locally.")
        hint.setWordWrap(True)
        hint.setStyleSheet("color: rgba(255,255,255,130); font-size: 11px;")
        layout.addWidget(hint)

        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        self._body = QtWidgets.QWidget()
        self._body_layout = QtWidgets.QVBoxLayout(self._body)
        self._body_layout.setContentsMargins(0, 4, 0, 0)
        self._body_layout.setSpacing(10)
        self._body_layout.addStretch(1)
        scroll.setWidget(self._body)
        layout.addWidget(scroll, 1)

        self._anim = QtCore.QPropertyAnimation(self, b"maximumWidth", self)
        self._anim.setDuration(240)
        self._anim.setEasingCurve(QtCore.QEasingCurve.Type.OutCubic)
        self._anim.finished.connect(self._on_anim_finished)

        self._apply_style()

    @property
    def is_open(self) -> bool:
        return self._open

    def refresh(self) -> None:
        self._render_sections(format_display_sections(self._get_store()))

    def set_open(self, open: bool, *, animate: bool = True) -> None:
        width_open = self.maximumWidth() > 8
        if open:
            if self._open and width_open:
                self.refresh()
                return
        elif not self._open and not width_open:
            return

        self._open = open
        if open:
            self.refresh()
        end = self.OPEN_WIDTH if open else 0
        if not animate:
            self._anim.stop()
            self.setMaximumWidth(end)
            self.setMinimumWidth(0 if not open else self.OPEN_WIDTH)
            self._notify_open_changed()
            return
        self._anim.stop()
        self._anim.setStartValue(self.maximumWidth())
        self._anim.setEndValue(end)
        self._anim.start()

    def toggle(self) -> bool:
        self.set_open(not self._open)
        return self._open

    def _on_anim_finished(self) -> None:
        if self._open:
            self.setMinimumWidth(self.OPEN_WIDTH)
        else:
            self.setMinimumWidth(0)
        self._notify_open_changed()

    def _notify_open_changed(self) -> None:
        if self._on_open_changed is not None:
            self._on_open_changed(self._open)

    def _render_sections(self, sections: list[MemoryDisplaySection]) -> None:
        while self._body_layout.count() > 1:
            item = self._body_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

        if not sections:
            empty = QtWidgets.QLabel(
                "Nothing stored yet.\n\nTry:\n• Remember my browser is Firefox\n• My name is Alex"
            )
            empty.setWordWrap(True)
            empty.setStyleSheet("color: rgba(255,255,255,175); font-size: 12px; line-height: 1.35;")
            self._body_layout.insertWidget(0, empty)
            return

        insert_at = 0
        for section in sections:
            heading = QtWidgets.QLabel(section.title)
            heading.setStyleSheet(
                "font-weight: 700; font-size: 12px; color: rgba(125,211,252,220); margin-top: 4px;"
            )
            self._body_layout.insertWidget(insert_at, heading)
            insert_at += 1

            list_widget = QtWidgets.QListWidget()
            list_widget.setFocusPolicy(QtCore.Qt.FocusPolicy.NoFocus)
            list_widget.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.NoSelection)
            list_widget.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            list_widget.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            for bullet in section.bullets:
                item = QtWidgets.QListWidgetItem(f"• {bullet}")
                item.setFlags(QtCore.Qt.ItemFlag.NoItemFlags)
                list_widget.addItem(item)
            row_h = max(1, list_widget.count()) * 22 + 8
            list_widget.setFixedHeight(min(row_h, 220))
            list_widget.setStyleSheet(
                "QListWidget { background: transparent; border: 0; padding: 0; }"
                "QListWidget::item { padding: 2px 0; color: rgba(243,244,246,220); }"
            )
            self._body_layout.insertWidget(insert_at, list_widget)
            insert_at += 1

    def _apply_style(self) -> None:
        self.setStyleSheet(
            """
            QFrame#memoryPanel {
              background: rgba(12, 18, 30, 242);
              border: 1px solid rgba(34, 211, 238, 70);
              border-left: 0px;
              border-top-right-radius: 14px;
              border-bottom-right-radius: 14px;
            }
            QScrollArea {
              background: transparent;
            }
            """
        )
