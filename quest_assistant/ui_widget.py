from __future__ import annotations

import math
import os
import random
import re
import sys
import threading
import time
import urllib.parse
import webbrowser
from dataclasses import dataclass
from typing import Callable, Optional

from PySide6 import QtCore, QtGui, QtWidgets

from quest_assistant.db import QuestDB, Task
from quest_assistant.local_llm import LLMAction, LocalLLMInterpreter
from quest_assistant.parser import (
    extract_add_titles,
    extract_quest_titles,
    has_add_intent,
    has_numbered_quest_markers,
    infer_casual_intent,
    normalize_quest_title,
    parse_action,
    parse_fx_enabled,
    parse_hide_intent,
    parse_quit_intent,
    split_into_items,
)
from quest_assistant.sound_effects import SoundEffects
from quest_assistant.speaker import Speaker
from quest_assistant.voice_listener import VoiceListener


def _hide_widget_from_taskbar(widget: QtWidgets.QWidget) -> None:
    if sys.platform != "win32":
        return
    try:
        import ctypes

        hwnd = int(widget.winId())
        gwl_exstyle = -20
        ws_ex_toolwindow = 0x00000080
        ws_ex_appwindow = 0x00040000
        style = ctypes.windll.user32.GetWindowLongW(hwnd, gwl_exstyle)
        style = (style | ws_ex_toolwindow) & ~ws_ex_appwindow
        ctypes.windll.user32.SetWindowLongW(hwnd, gwl_exstyle, style)
    except Exception:
        pass


@dataclass
class UIState:
    listening_requested: bool = True
    visuals_enabled: bool = False
    jarvis_awake: bool = False


class PrivacyListenButton(QtWidgets.QWidget):
    _DIAMETER = 168

    def __init__(self, on_enable: Callable[[], None]) -> None:
        super().__init__()
        self._on_enable = on_enable
        self._drag_pos: Optional[QtCore.QPoint] = None
        self._press_pos: Optional[QtCore.QPoint] = None
        self._dragged = False
        self._hovered = False
        self._pressed = False

        self.setWindowTitle("Jarvis Privacy Mode")
        self.setWindowFlags(
            QtCore.Qt.WindowType.FramelessWindowHint
            | QtCore.Qt.WindowType.Tool
            | QtCore.Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setFixedSize(self._DIAMETER, self._DIAMETER)
        self.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        self._apply_circular_mask()

    def _apply_circular_mask(self) -> None:
        region = QtGui.QRegion(0, 0, self._DIAMETER, self._DIAMETER, QtGui.QRegion.RegionType.Ellipse)
        self.setMask(region)

    def _circle_rect(self) -> QtCore.QRectF:
        inset = 3.0
        return QtCore.QRectF(inset, inset, self._DIAMETER - (inset * 2), self._DIAMETER - (inset * 2))

    def _in_circle(self, pos: QtCore.QPoint) -> bool:
        rect = self._circle_rect()
        center = rect.center()
        radius = rect.width() / 2.0
        dx = pos.x() - center.x()
        dy = pos.y() - center.y()
        return (dx * dx + dy * dy) <= (radius * radius)

    def _fill_gradient(self) -> QtGui.QRadialGradient:
        rect = self._circle_rect()
        gradient = QtGui.QRadialGradient(
            rect.left() + (rect.width() * 0.32),
            rect.top() + (rect.height() * 0.28),
            rect.width() * 0.88,
        )
        if self._pressed:
            gradient.setColorAt(0.0, QtGui.QColor("#fca5a5"))
            gradient.setColorAt(0.35, QtGui.QColor("#ef4444"))
            gradient.setColorAt(0.78, QtGui.QColor("#dc2626"))
            gradient.setColorAt(1.0, QtGui.QColor("#c81e1e"))
        elif self._hovered:
            gradient.setColorAt(0.0, QtGui.QColor("#ffe4e6"))
            gradient.setColorAt(0.22, QtGui.QColor("#fb7185"))
            gradient.setColorAt(0.62, QtGui.QColor("#ef4444"))
            gradient.setColorAt(1.0, QtGui.QColor("#dc2626"))
        else:
            gradient.setColorAt(0.0, QtGui.QColor("#fecdd3"))
            gradient.setColorAt(0.24, QtGui.QColor("#f87171"))
            gradient.setColorAt(0.62, QtGui.QColor("#ef4444"))
            gradient.setColorAt(1.0, QtGui.QColor("#dc2626"))
        return gradient

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:  # noqa: N802
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)

        # Clear the square widget backing store so corners stay fully transparent.
        painter.setCompositionMode(QtGui.QPainter.CompositionMode.CompositionMode_Source)
        painter.fillRect(self.rect(), QtCore.Qt.GlobalColor.transparent)
        painter.setCompositionMode(QtGui.QPainter.CompositionMode.CompositionMode_SourceOver)

        rect = self._circle_rect()
        if self._pressed:
            rect.translate(1.0, 1.0)

        painter.setPen(QtCore.Qt.PenStyle.NoPen)
        painter.setBrush(self._fill_gradient())
        painter.drawEllipse(rect)

        # Soft inner highlight for a rounded 3D look.
        highlight = QtGui.QRadialGradient(
            rect.left() + (rect.width() * 0.34),
            rect.top() + (rect.height() * 0.26),
            rect.width() * 0.55,
        )
        highlight.setColorAt(0.0, QtGui.QColor(255, 255, 255, 95))
        highlight.setColorAt(0.55, QtGui.QColor(255, 255, 255, 18))
        highlight.setColorAt(1.0, QtGui.QColor(255, 255, 255, 0))
        painter.setBrush(highlight)
        painter.drawEllipse(rect)

        border_alpha = 235 if self._hovered else 210
        border = QtGui.QPen(QtGui.QColor(255, 255, 255, border_alpha), 2.5)
        painter.setPen(border)
        painter.setBrush(QtCore.Qt.BrushStyle.NoBrush)
        painter.drawEllipse(rect)

        title_font = QtGui.QFont()
        title_font.setBold(True)
        title_font.setPointSize(13)
        subtitle_font = QtGui.QFont()
        subtitle_font.setBold(True)
        subtitle_font.setPointSize(9)

        title_rect = QtCore.QRectF(rect.left(), rect.top() + (rect.height() * 0.28), rect.width(), rect.height() * 0.24)
        subtitle_rect = QtCore.QRectF(rect.left() + 8, rect.top() + (rect.height() * 0.52), rect.width() - 16, rect.height() * 0.28)

        painter.setPen(QtGui.QColor("#ffffff"))
        painter.setFont(title_font)
        painter.drawText(title_rect, QtCore.Qt.AlignmentFlag.AlignHCenter | QtCore.Qt.AlignmentFlag.AlignVCenter, "MIC OFF")
        painter.setFont(subtitle_font)
        painter.drawText(
            subtitle_rect,
            QtCore.Qt.AlignmentFlag.AlignHCenter | QtCore.Qt.AlignmentFlag.AlignTop | QtCore.Qt.TextFlag.TextWordWrap,
            "Click to listen",
        )

    def show_near_bottom_right(self) -> None:
        screen = QtGui.QGuiApplication.primaryScreen()
        if screen:
            area = screen.availableGeometry()
            self.move(area.right() - self.width() - 24, area.bottom() - self.height() - 24)
        self.show()
        self.raise_()
        _hide_widget_from_taskbar(self)

    def enterEvent(self, event: QtCore.QEvent) -> None:  # noqa: N802
        self._hovered = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event: QtCore.QEvent) -> None:  # noqa: N802
        self._hovered = False
        self._pressed = False
        self.update()
        super().leaveEvent(event)

    def mousePressEvent(self, event: QtGui.QMouseEvent) -> None:  # noqa: N802
        if event.button() == QtCore.Qt.MouseButton.LeftButton and self._in_circle(event.position().toPoint()):
            global_pos = event.globalPosition().toPoint()
            self._press_pos = global_pos
            self._drag_pos = global_pos - self.frameGeometry().topLeft()
            self._dragged = False
            self._pressed = True
            self.update()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QtGui.QMouseEvent) -> None:  # noqa: N802
        if self._drag_pos is not None and event.buttons() & QtCore.Qt.MouseButton.LeftButton:
            global_pos = event.globalPosition().toPoint()
            if self._press_pos and (global_pos - self._press_pos).manhattanLength() > 4:
                self._dragged = True
            self.move(global_pos - self._drag_pos)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QtGui.QMouseEvent) -> None:  # noqa: N802
        if event.button() == QtCore.Qt.MouseButton.LeftButton:
            was_dragged = self._dragged
            in_circle = self._in_circle(event.position().toPoint())
            self._drag_pos = None
            self._press_pos = None
            self._dragged = False
            self._pressed = False
            self.update()
            event.accept()
            if not was_dragged and in_circle:
                self._on_enable()
            return
        super().mouseReleaseEvent(event)


class AIFXBackground(QtWidgets.QFrame):
    def __init__(self) -> None:
        super().__init__()
        self.enabled = False
        self.phase = 0.0
        self._rng = random.Random()
        self._signals: list[dict] = []
        self._update_pending = False
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

    def set_visuals(self, enabled: bool) -> None:
        self.enabled = enabled
        if not enabled:
            self._signals.clear()
            self._update_pending = False
        self._request_update()

    def set_phase(self, phase: float) -> None:
        self.phase = phase
        if not self.enabled:
            return
        self._advance_signals()
        self._request_update()

    def _request_update(self) -> None:
        if not self.enabled or not self.isVisible():
            return
        if self.width() <= 0 or self.height() <= 0:
            return
        if self._update_pending:
            return
        self._update_pending = True
        QtCore.QTimer.singleShot(0, self._flush_update)

    def _flush_update(self) -> None:
        self._update_pending = False
        if self.enabled and self.isVisible():
            self.update()

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:  # noqa: N802
        super().paintEvent(event)
        if not self.enabled:
            return
        width = self.width()
        height = self.height()
        if width <= 0 or height <= 0:
            return
        try:
            with QtGui.QPainter(self) as painter:
                if not painter.isActive():
                    return

                glow = QtGui.QRadialGradient(QtCore.QPointF(width * 0.18, height * 0.12), width * 0.75)
                glow.setColorAt(0.0, QtGui.QColor(34, 211, 238, 36))
                glow.setColorAt(0.45, QtGui.QColor(59, 130, 246, 14))
                glow.setColorAt(1.0, QtGui.QColor(0, 0, 0, 0))
                painter.fillRect(self.rect(), QtGui.QBrush(glow))

                for signal in self._signals:
                    self._draw_signal(painter, signal, width, height)
        except Exception:
            return

    def _advance_signals(self) -> None:
        self._signals = [s for s in self._signals if s["progress"] - s["length"] <= 1.02]
        for signal in self._signals:
            signal["progress"] += signal["speed"]

        if len(self._signals) < 6 and self._rng.random() < 0.28:
            self._signals.append(self._new_signal())

    def _new_signal(self) -> dict:
        edge = self._rng.choice(("left", "right", "top", "bottom"))
        if edge == "left":
            start = (-0.08, self._rng.random())
            direction = (1, 0)
        elif edge == "right":
            start = (1.08, self._rng.random())
            direction = (-1, 0)
        elif edge == "top":
            start = (self._rng.random(), -0.08)
            direction = (0, 1)
        else:
            start = (self._rng.random(), 1.08)
            direction = (0, -1)

        points = [start]
        x, y = start
        dx, dy = direction
        for _ in range(self._rng.randint(2, 4)):
            distance = self._rng.uniform(0.16, 0.38)
            x += dx * distance
            y += dy * distance
            points.append((x, y))
            if self._rng.random() < 0.72:
                dx, dy = self._rng.choice(((dy, dx), (-dy, -dx)))

        x += dx * 0.35
        y += dy * 0.35
        points.append((x, y))

        return {
            "points": points,
            "speed": self._rng.uniform(0.032, 0.065),
            "progress": -self._rng.uniform(0.04, 0.14),
            "length": self._rng.uniform(0.030, 0.060),
            "color": self._rng.choice(
                (
                    QtGui.QColor(74, 222, 128),
                    QtGui.QColor(34, 197, 94),
                    QtGui.QColor(45, 212, 191),
                    QtGui.QColor(34, 211, 238),
                    QtGui.QColor(56, 189, 248),
                    QtGui.QColor(96, 165, 250),
                )
            ),
        }

    def _draw_signal(self, painter: QtGui.QPainter, signal: dict, width: int, height: int) -> None:
        points = [QtCore.QPointF(x * width, y * height) for x, y in signal["points"]]
        if len(points) < 2:
            return

        total = self._path_length(points)
        if total <= 0:
            return

        head = signal["progress"]
        tail = head - signal["length"]
        color: QtGui.QColor = signal["color"]

        start = max(0.0, tail)
        end = min(1.0, head)
        if end <= start:
            return

        pen = QtGui.QPen(QtGui.QColor(color.red(), color.green(), color.blue(), 175), 2)
        pen.setCapStyle(QtCore.Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(QtCore.Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        self._draw_path_slice(painter, points, total, start, end)

    def _draw_path_slice(
        self,
        painter: QtGui.QPainter,
        points: list[QtCore.QPointF],
        total: float,
        start: float,
        end: float,
    ) -> None:
        if end <= 0 or start >= 1 or start >= end:
            return

        current = start
        guard = 0
        while current < end:
            guard += 1
            if guard > 64:
                break
            p1 = self._point_at(points, total, current)
            next_boundary = self._next_corner_progress(points, total, current)
            segment_end = min(end, next_boundary)
            if segment_end <= current:
                segment_end = min(end, current + 0.01)
            p2 = self._point_at(points, total, segment_end)
            painter.drawLine(p1, p2)
            current = segment_end + 0.001

    @staticmethod
    def _path_length(points: list[QtCore.QPointF]) -> float:
        return sum(QtCore.QLineF(points[i], points[i + 1]).length() for i in range(len(points) - 1))

    @staticmethod
    def _point_at(points: list[QtCore.QPointF], total: float, progress: float) -> QtCore.QPointF:
        target = progress * total
        walked = 0.0
        for i in range(len(points) - 1):
            segment = QtCore.QLineF(points[i], points[i + 1])
            length = segment.length()
            if walked + length >= target:
                ratio = (target - walked) / max(length, 0.001)
                return QtCore.QPointF(
                    points[i].x() + ((points[i + 1].x() - points[i].x()) * ratio),
                    points[i].y() + ((points[i + 1].y() - points[i].y()) * ratio),
                )
            walked += length
        return points[-1]

    @staticmethod
    def _next_corner_progress(points: list[QtCore.QPointF], total: float, progress: float) -> float:
        target = progress * total
        walked = 0.0
        for i in range(len(points) - 1):
            length = QtCore.QLineF(points[i], points[i + 1]).length()
            if walked + length > target:
                return min(1.0, (walked + length) / total)
            walked += length
        return 1.0


class QuestWidget(QtWidgets.QWidget):
    _ADD_MODE_TIMEOUT_S = 18.0
    _VOICE_DEDUP_S = 0.9
    _CONTROL_COOLDOWN_S = 0.8
    _ECHO_GUARD_S = 4.5
    _LLM_CONTROL_KINDS = frozenset(
        {"show", "hide", "listen_off", "add_done", "quit", "set_fx", "open_browser", "web_search"}
    )

    _VOICE_GRACE_AFTER_SPEECH_S = 1.4
    _VOICE_FILLER_WORDS = frozenset(
        {
            "a",
            "ah",
            "an",
            "and",
            "hmm",
            "hm",
            "i",
            "im",
            "listening",
            "okay",
            "ok",
            "oh",
            "sir",
            "the",
            "uh",
            "um",
            "yeah",
            "yes",
            "you",
        }
    )

    # Delivered from a background worker thread -> queued onto the UI thread.
    _llm_result_ready = QtCore.Signal(int, str, str, object)

    def __init__(self, db: QuestDB) -> None:
        super().__init__()
        self.db = db
        self.state = UIState()
        self._pending_voice_add = False
        self._pending_voice_add_until = 0.0
        self._last_voice_text = ""
        self._last_voice_at = 0.0
        self._last_control_kind = ""
        self._last_control_at = 0.0
        self._show_pending = False
        self._hide_pending = False
        self._spoke_this_turn = False
        self._spoken_echo_guard: list[tuple[float, str]] = []
        self._last_command_text = ""
        self._last_core_alpha = -1
        self._voice_grace_until = 0.0
        self._llm_busy = False
        self._llm_seq = 0
        self._refresh_pending = False
        self._refresh_dirty = False
        self._voice_restart_at = 0.0
        self._llm_result_ready.connect(self._on_llm_result)

        self.setWindowTitle("Assistance")
        self.setWindowFlags(
            QtCore.Qt.WindowType.FramelessWindowHint
            | QtCore.Qt.WindowType.Tool
            | QtCore.Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_TranslucentBackground, True)

        self._drag_pos: Optional[QtCore.QPoint] = None
        self._visual_phase = 0.0

        self.brain = LocalLLMInterpreter()
        self.sfx = SoundEffects()
        self.speaker = Speaker(on_speak_state=self._on_speaker_state)
        self.speaker.preload(
            [
                "Yes, sir.",
                "Going quiet, sir.",
                "Mic off, sir.",
                "Effects on.",
                "Effects off.",
                "Opening browser.",
                "Searching.",
                "Goodbye, sir.",
                "Listening, sir.",
            ]
        )
        self.voice = VoiceListener(on_text=self._on_voice_text, wake_word="jarvis", command_window_s=8.0)
        self.privacy_button = PrivacyListenButton(
            on_enable=lambda: self.enable_listening(announce=True),
        )

        self._build_ui()
        self.refresh()

        self._timer = QtCore.QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(500)

        self._visual_timer = QtCore.QTimer(self)
        self._visual_timer.timeout.connect(self._animate_visuals)
        self._visual_timer.setInterval(250)

        # Always listening in the background (wake word only while hidden).
        self.enable_listening(announce=False)
        self._sync_voice_gate()
        self._sync_privacy_button()

    def _build_ui(self) -> None:
        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(12, 12, 12, 12)

        self.card = QtWidgets.QFrame()
        self.card.setObjectName("card")
        self._apply_widget_style()

        stack = QtWidgets.QStackedLayout(self.card)
        stack.setStackingMode(QtWidgets.QStackedLayout.StackingMode.StackAll)
        stack.setContentsMargins(0, 0, 0, 0)

        self.fx_background = AIFXBackground()
        stack.addWidget(self.fx_background)

        content = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(content)
        layout.setContentsMargins(14, 14, 14, 14)
        stack.addWidget(content)
        outer.addWidget(self.card)

        header = QtWidgets.QHBoxLayout()
        layout.addLayout(header)

        title = QtWidgets.QLabel("Assistance")
        title.setStyleSheet("font-size: 16px; font-weight: 700;")
        header.addWidget(title)

        self.core_label = QtWidgets.QLabel("AI")
        self.core_label.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        self.core_label.setFixedSize(34, 34)
        header.addWidget(self.core_label)

        header.addStretch(1)

        self.visuals_btn = QtWidgets.QPushButton("AI FX: OFF")
        self.visuals_btn.setCheckable(True)
        self.visuals_btn.clicked.connect(self._toggle_visuals)
        header.addWidget(self.visuals_btn)

        self.listen_btn = QtWidgets.QPushButton("Listening: OFF")
        self.listen_btn.setCheckable(True)
        self.listen_btn.clicked.connect(self._toggle_listening)
        header.addWidget(self.listen_btn)

        add_row = QtWidgets.QHBoxLayout()
        layout.addLayout(add_row)

        self.input = QtWidgets.QLineEdit()
        self.input.setPlaceholderText("Add quests… e.g. “wash dishes, workout at 7pm”")
        self.input.returnPressed.connect(self._handle_input)
        add_row.addWidget(self.input, 1)

        add_btn = QtWidgets.QPushButton("Add")
        add_btn.clicked.connect(self._handle_input)
        add_row.addWidget(add_btn)

        open_header = QtWidgets.QHBoxLayout()
        layout.addLayout(open_header)

        self.open_label = QtWidgets.QLabel("Open")
        self.open_label.setStyleSheet("font-weight: 700; margin-top: 6px;")
        open_header.addWidget(self.open_label)
        open_header.addStretch(1)

        delete_open_btn = QtWidgets.QPushButton("Delete selected")
        delete_open_btn.clicked.connect(lambda: self._delete_selected_from(self.open_list))
        open_header.addWidget(delete_open_btn)

        self.open_list = QtWidgets.QListWidget()
        self.open_list.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.ExtendedSelection)
        self.open_list.setWordWrap(True)
        self.open_list.setUniformItemSizes(False)
        self.open_list.itemChanged.connect(self._on_item_changed)
        layout.addWidget(self.open_list, 1)

        done_header = QtWidgets.QHBoxLayout()
        layout.addLayout(done_header)

        self.done_label = QtWidgets.QLabel("Done")
        self.done_label.setStyleSheet("font-weight: 700; margin-top: 6px;")
        done_header.addWidget(self.done_label)
        done_header.addStretch(1)

        delete_done_btn = QtWidgets.QPushButton("Delete selected")
        delete_done_btn.clicked.connect(lambda: self._delete_selected_from(self.done_list))
        done_header.addWidget(delete_done_btn)

        self.done_list = QtWidgets.QListWidget()
        self.done_list.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.ExtendedSelection)
        self.done_list.setWordWrap(True)
        self.done_list.setUniformItemSizes(False)
        layout.addWidget(self.done_list, 1)

        self.footer = QtWidgets.QLabel(self._default_footer_text())
        self.footer.setStyleSheet("color: rgba(255,255,255,140); margin-top: 6px;")
        layout.addWidget(self.footer)

        self.resize(380, 520)
        self._apply_visual_core_style(0.0)

    def _tick(self) -> None:
        self._expire_pending_add_if_needed()
        running = self.voice.is_running
        if self.state.listening_requested and not running:
            self.listen_btn.setText("Listening: STARTING…")
            now = time.monotonic()
            if now - self._voice_restart_at > 3.0:
                self._voice_restart_at = now
                self.voice.restart()
        elif self.state.listening_requested:
            self.listen_btn.setText("Listening: ON")
            self.listen_btn.setChecked(True)
        else:
            self.listen_btn.setText("Listening: OFF")
            self.listen_btn.setChecked(False)
        self._sync_privacy_button()

    def _toggle_visuals(self) -> None:
        self._set_visuals_enabled(self.visuals_btn.isChecked())

    def _apply_fx_enabled(self, enabled: bool) -> None:
        try:
            self._set_visuals_enabled(enabled)
        except Exception:
            self._visual_timer.stop()
            self.state.visuals_enabled = False

    def _set_visuals_enabled(self, enabled: bool) -> None:
        try:
            self.state.visuals_enabled = enabled
            self.visuals_btn.blockSignals(True)
            self.visuals_btn.setChecked(enabled)
            self.visuals_btn.blockSignals(False)
            self.visuals_btn.setText("AI FX: ON" if enabled else "AI FX: OFF")
            self._apply_widget_style()
            self.fx_background.set_visuals(enabled)

            if enabled:
                self._last_core_alpha = -1
                self._apply_visual_core_style(1.0)
                if not self._visual_timer.isActive():
                    self._visual_timer.start()
            else:
                self._visual_timer.stop()
                self._visual_phase = 0.0
                self.fx_background.set_phase(0.0)
                self._last_core_alpha = -1
                self._apply_visual_core_style(0.0)
        except Exception:
            self._visual_timer.stop()
            self.state.visuals_enabled = False

    def _animate_visuals(self) -> None:
        if not self.state.visuals_enabled or not self.isVisible():
            return
        try:
            self._visual_phase = (self._visual_phase + 0.16) % (math.pi * 2)
            self.fx_background.set_phase(self._visual_phase)
        except Exception:
            self._visual_timer.stop()
            self.state.visuals_enabled = False
            self.fx_background.set_visuals(False)

    def _apply_widget_style(self) -> None:
        if getattr(self.state, "visuals_enabled", False):
            card_border = "rgba(34,211,238,115)"
            card_bg = "rgba(8, 16, 28, 235)"
            button_bg = "rgba(14,165,233,175)"
            checked_bg = "rgba(34,197,94,185)"
            input_bg = "rgba(15,23,42,210)"
        else:
            card_border = "rgba(255,255,255,25)"
            card_bg = "rgba(20, 20, 25, 230)"
            button_bg = "rgba(99,102,241,180)"
            checked_bg = "rgba(34,197,94,170)"
            input_bg = "rgba(255,255,255,18)"

        self.card.setStyleSheet(
            f"""
            QFrame#card {{
              background: {card_bg};
              border: 1px solid {card_border};
              border-radius: 14px;
            }}
            QLabel, QPushButton, QLineEdit, QListWidget {{
              color: #f3f4f6;
              font-size: 12px;
            }}
            QLineEdit {{
              background: {input_bg};
              border: 1px solid rgba(255,255,255,28);
              border-radius: 10px;
              padding: 8px 10px;
            }}
            QPushButton {{
              background: {button_bg};
              border: 1px solid rgba(255,255,255,24);
              border-radius: 10px;
              padding: 8px 10px;
            }}
            QPushButton:checked {{
              background: {checked_bg};
            }}
            QListWidget {{
              background: transparent;
              border: 0px;
            }}
            """
        )

    def _apply_visual_core_style(self, pulse: float) -> None:
        if getattr(self.state, "visuals_enabled", False):
            alpha = int(130 + (pulse * 90))
            self.core_label.setText("AI")
            self.core_label.setStyleSheet(
                f"""
                QLabel {{
                  color: rgb(224, 242, 254);
                  background: qradialgradient(cx:0.5, cy:0.5, radius:0.7,
                    stop:0 rgba(34,211,238,{alpha}),
                    stop:0.45 rgba(59,130,246,95),
                    stop:1 rgba(15,23,42,40));
                  border: 1px solid rgba(125, 211, 252, {alpha});
                  border-radius: 17px;
                  font-size: 11px;
                  font-weight: 800;
                }}
                """
            )
        else:
            self.core_label.setText("AI")
            self.core_label.setStyleSheet(
                """
                QLabel {
                  color: rgba(255,255,255,155);
                  background: rgba(255,255,255,18);
                  border: 1px solid rgba(255,255,255,25);
                  border-radius: 17px;
                  font-size: 11px;
                  font-weight: 800;
                }
                """
            )

    def refresh(self) -> None:
        self._refresh_dirty = True
        if self._refresh_pending:
            return
        self._refresh_pending = True
        QtCore.QTimer.singleShot(0, self._refresh_now)

    def _refresh_now(self) -> None:
        self._refresh_pending = False
        if not self._refresh_dirty:
            return
        self._refresh_dirty = False
        try:
            self.open_list.blockSignals(True)
            self.open_list.clear()
            for index, t in enumerate(self.db.list_tasks(status="open"), start=1):
                label = f"{index}. {t.title}"
                item = QtWidgets.QListWidgetItem(label)
                item.setData(QtCore.Qt.ItemDataRole.UserRole, t.id)
                item.setFlags(item.flags() | QtCore.Qt.ItemFlag.ItemIsUserCheckable)
                item.setCheckState(QtCore.Qt.CheckState.Unchecked)
                item.setSizeHint(self._size_hint_for_title(label))
                self.open_list.addItem(item)
            self.open_list.blockSignals(False)

            self.done_list.clear()
            for t in self.db.list_tasks(status="done"):
                item = QtWidgets.QListWidgetItem(f"{t.title}")
                item.setData(QtCore.Qt.ItemDataRole.UserRole, t.id)
                item.setFlags(item.flags() & ~QtCore.Qt.ItemFlag.ItemIsEditable)
                item.setSizeHint(self._size_hint_for_title(t.title))
                self.done_list.addItem(item)
        except Exception:
            pass
        if self._refresh_dirty:
            self.refresh()

    def _handle_input(self) -> None:
        text = self.input.text().strip()
        if not text:
            return
        self.input.clear()
        self._apply_text(text, source="typed")

    def _apply_text(self, text: str, source: str) -> None:
        try:
            self._apply_text_impl(text, source=source)
        except Exception:
            return

    def _apply_text_impl(self, text: str, source: str) -> None:
        self._spoke_this_turn = False
        self._last_command_text = text
        self._llm_seq += 1
        self._expire_pending_add_if_needed()
        if self._pending_voice_add and self._should_cancel_pending_add(text):
            self._set_pending_add(False)
            self._set_footer_default()

        # 1) Instant, deterministic handling (no network, never freezes the UI).
        if self._try_apply_parser_control_action(text):
            self.refresh()
            return
        if self._try_apply_casual_intent(text):
            self.refresh()
            return
        if self._apply_parser_actions(text, source):
            self.refresh()
            return

        # 2) Natural-language understanding via the local model, OFF the UI thread.
        if self.brain.is_enabled and self._should_dispatch_llm(text):
            if self._dispatch_llm(text, source, self._llm_seq):
                return

        self.refresh()

    def _apply_parser_actions(self, text: str, source: str) -> bool:
        matched_parser = False
        items = [text] if extract_add_titles(text) or self._pending_voice_add else split_into_items(text)
        for item in items:
            action = parse_action(
                item,
                allow_implicit_add=source == "typed" or self._pending_voice_add,
            )
            if action.kind != "noop":
                matched_parser = True

            if action.kind in {"show", "hide", "listen_off", "add_done", "quit"}:
                self._apply_nonquest_llm_action(LLMAction(kind=action.kind, title=action.title))
                if action.kind == "quit":
                    return True
                continue

            if action.kind == "add":
                titles = extract_quest_titles(item) if self._pending_voice_add else extract_add_titles(item)
                if not titles and action.title:
                    titles = [action.title]
                titles = [t for t in (normalize_quest_title(title) for title in titles) if t]

                if titles:
                    for title in titles:
                        self.db.add_task(
                            title,
                            due_iso=action.due_iso,
                            source=source,
                            raw_input=action.raw,
                        )
                    self.sfx.play("add")
                    if len(titles) == 1:
                        self._jarvis_say(f"Done, sir. I added the quest: {titles[0]}.")
                    else:
                        self._jarvis_say(f"Done, sir. I added {len(titles)} quests.")
                    should_continue_collection = (
                        source == "voice"
                        and self._pending_voice_add
                        and len(titles) == 1
                        and has_numbered_quest_markers(item)
                    )
                    self._set_pending_add(should_continue_collection)
                    if should_continue_collection:
                        self.footer.setText('Keep saying the next quest, or say "Jarvis stop adding".')
                    else:
                        self._set_footer_default()
                else:
                    # Supports: "Jarvis, add a quest" -> next utterance becomes the quest title.
                    self._set_pending_add(True)
                    self.footer.setText('Say the quest naturally, or say "next quest..." for more.')
                    self._jarvis_say("Of course. Tell me the quest, or say next quest for more.")
                continue

            if action.kind == "complete" and (action.title or action.quest_number is not None):
                self._complete_open_quest(title=action.title, number=action.quest_number)
                continue

            if action.kind == "delete" and (action.title or action.quest_number is not None):
                self._delete_open_quest(title=action.title, number=action.quest_number)
                continue

        return matched_parser or self._spoke_this_turn

    @staticmethod
    def _should_dispatch_llm(text: str) -> bool:
        cleaned = " ".join((text or "").split())
        if len(cleaned) < 6:
            return False
        words = [w.strip(".,!?;:") for w in cleaned.lower().split()]
        if len(words) < 2:
            return False
        if all(word in QuestWidget._VOICE_FILLER_WORDS for word in words):
            return False
        # Skip the model for obvious control phrases the parser already understands.
        if parse_fx_enabled(text) is not None:
            return False
        if parse_hide_intent(text) or parse_quit_intent(text):
            return False
        if has_add_intent(text):
            return False
        quick = parse_action(text, allow_implicit_add=False)
        if quick.kind in {"show", "hide", "listen_off", "add_done", "quit", "complete", "delete", "add"}:
            return False
        return True

    def _dispatch_llm(self, text: str, source: str, seq: int) -> bool:
        if self._llm_busy:
            return False
        self._llm_busy = True
        pending_add = self._pending_voice_add
        jarvis_awake = self.state.jarvis_awake
        open_quests = self._open_quests_for_llm()

        def worker() -> None:
            result = None
            try:
                result = self.brain.interpret(
                    text,
                    pending_add=pending_add,
                    jarvis_awake=jarvis_awake,
                    open_quests=open_quests,
                    source=source,
                )
            except Exception:
                result = None
            finally:
                try:
                    self._llm_result_ready.emit(seq, text, source, result)
                except Exception:
                    pass

        threading.Thread(target=worker, daemon=True).start()
        return True

    @QtCore.Slot(int, str, str, object)
    def _on_llm_result(self, seq: int, text: str, source: str, result: object) -> None:
        self._llm_busy = False
        try:
            if seq != self._llm_seq:
                return
            self._spoke_this_turn = False
            self._last_command_text = text
            if result is not None and self._apply_llm_result(text, source, result):
                self.refresh()
                return
            # Model returned nothing usable -> last-resort deterministic pass.
            if self._apply_parser_actions(text, source):
                self.refresh()
                return
            self.refresh()
        except Exception:
            pass

    def _finish_quest_mutation(self) -> None:
        self._set_pending_add(False)
        self._set_footer_default()

    @staticmethod
    def _quest_number_from_llm_title(title: str) -> Optional[int]:
        cleaned = (title or "").strip().lstrip("#").strip()
        if cleaned.isdigit():
            value = int(cleaned)
            return value if value > 0 else None
        # Handle "task 2", "quest 3", "number 4", "#2".
        match = re.fullmatch(r"(?:task|quest|mission|number|item|no\.?)?\s*#?\s*(\d+)", cleaned, re.IGNORECASE)
        if match:
            value = int(match.group(1))
            return value if value > 0 else None
        return None

    def _resolve_open_quest(
        self,
        *,
        title: Optional[str] = None,
        number: Optional[int] = None,
    ) -> Optional[Task]:
        if number is not None:
            return self.db.get_open_task_by_number(number)
        if title:
            matches = self.db.find_open_by_title_contains(title, limit=1)
            return matches[0] if matches else None
        return None

    def _complete_open_quest(
        self,
        *,
        title: Optional[str] = None,
        number: Optional[int] = None,
    ) -> None:
        task = self._resolve_open_quest(title=title, number=number)
        if task:
            self.db.set_status(task.id, "done")
            self.sfx.play("complete")
            if number is not None:
                self._jarvis_say(f"Task {number} completed: {task.title}.")
            else:
                self._jarvis_say(f"Quest completed: {task.title}.")
        else:
            self.sfx.play("error")
            if number is not None:
                self._jarvis_say(f"I could not find open task {number}, sir.")
            else:
                self._jarvis_say(f"I could not find an open quest matching {title}.")
        self._finish_quest_mutation()

    def _delete_open_quest(
        self,
        *,
        title: Optional[str] = None,
        number: Optional[int] = None,
    ) -> None:
        task: Optional[Task] = None
        if number is not None:
            task = self.db.get_open_task_by_number(number)
        elif title:
            matches = self.db.find_by_title_contains(title, limit=1)
            task = matches[0] if matches else None

        if task:
            self.db.delete_task(task.id)
            self.sfx.play("delete")
            if number is not None:
                self._jarvis_say(f"Task {number} deleted: {task.title}.")
            else:
                self._jarvis_say(f"Quest deleted: {task.title}.")
        else:
            self.sfx.play("error")
            if number is not None:
                self._jarvis_say(f"I could not find open task {number}, sir.")
            else:
                self._jarvis_say(f"I could not find an open quest matching {title}.")
        self._finish_quest_mutation()

    def _try_apply_parser_control_action(self, text: str) -> bool:
        fx_enabled = parse_fx_enabled(text)
        if fx_enabled is not None:
            return self._apply_nonquest_llm_action(
                LLMAction(kind="set_fx", value="on" if fx_enabled else "off")
            )

        if parse_hide_intent(text):
            return self._apply_nonquest_llm_action(LLMAction(kind="hide"))

        if parse_quit_intent(text):
            return self._apply_nonquest_llm_action(LLMAction(kind="quit"))

        action = parse_action(text, allow_implicit_add=False)
        if action.kind not in {"show", "hide", "listen_off", "add_done", "quit"}:
            return False
        return self._apply_nonquest_llm_action(LLMAction(kind=action.kind, title=action.title))

    def _open_quests_for_llm(self) -> list[dict[str, object]]:
        return [
            {"number": index, "title": task.title}
            for index, task in enumerate(self.db.list_tasks(status="open"), start=1)
        ]

    def _try_apply_casual_intent(self, text: str) -> bool:
        action = infer_casual_intent(text)
        if not action:
            return False
        if action.kind == "set_fx":
            return self._apply_nonquest_llm_action(
                LLMAction(kind="set_fx", value=action.value or "on")
            )
        if action.kind == "quit":
            return parse_quit_intent(text) and self._apply_nonquest_llm_action(LLMAction(kind="quit"))
        if action.kind in {"show", "hide", "listen_off"}:
            return self._apply_nonquest_llm_action(LLMAction(kind=action.kind))
        return False

    def _try_apply_llm_actions(self, text: str, *, source: str) -> bool:
        result = self.brain.interpret(
            text,
            pending_add=self._pending_voice_add,
            jarvis_awake=self.state.jarvis_awake,
            open_quests=self._open_quests_for_llm(),
            source=source,
        )
        if not result:
            return False
        return self._apply_llm_result(text, source, result)

    def _apply_llm_result(self, text: str, source: str, result: object) -> bool:
        if result is None:
            return False
        result_actions = getattr(result, "actions", None)
        if not result_actions:
            return False

        actions = [a for a in result_actions if a.kind != "noop"]
        if not actions:
            if self.state.jarvis_awake and source == "voice":
                replies = [a.value for a in result_actions if a.kind == "reply" and a.value]
                if replies:
                    self._jarvis_say(" ".join(replies[:2]))
                    return True
            return False

        allow_add_actions = self._pending_voice_add or has_add_intent(text) or bool(extract_add_titles(text))
        add_titles: list[str] = []
        completed = 0
        deleted = 0
        replies: list[str] = []
        handled = False
        control_used = False

        for action in actions:
            if action.kind in self._LLM_CONTROL_KINDS:
                if control_used:
                    continue
                control_used = True
            if action.kind == "quit" and not parse_quit_intent(text):
                if parse_hide_intent(text):
                    if self._apply_nonquest_llm_action(LLMAction(kind="hide")):
                        handled = True
                continue
            if self._apply_nonquest_llm_action(action):
                handled = True
                continue

            if action.kind == "add":
                if not allow_add_actions:
                    continue
                if action.title:
                    title = normalize_quest_title(action.title)
                    if title:
                        add_titles.append(title)
                else:
                    self._set_pending_add(True)
                    self.footer.setText('Say the quest naturally, or say "next quest..." for more.')
                continue

            if action.kind == "complete":
                title = action.title
                number = self._quest_number_from_llm_title(title or "")
                if number is not None:
                    title = None
                if number is not None or title:
                    task = self._resolve_open_quest(title=title, number=number)
                    if task:
                        self.db.set_status(task.id, "done")
                        completed += 1
                continue

            if action.kind == "delete":
                number = self._quest_number_from_llm_title(action.title or "")
                title = None if number is not None else action.title
                if number is not None:
                    task = self.db.get_open_task_by_number(number)
                    if task:
                        self.db.delete_task(task.id)
                        deleted += 1
                elif title:
                    matches = self.db.find_by_title_contains(title, limit=1)
                    if matches:
                        self.db.delete_task(matches[0].id)
                        deleted += 1
                continue

            if action.kind == "reply" and action.value:
                replies.append(action.value)
                continue

        for title in add_titles:
            self.db.add_task(title, source=f"{source}:llm", raw_input=text)

        if add_titles:
            handled = True
            self.sfx.play("add")
            self._set_pending_add(False)
            if len(add_titles) == 1:
                self._jarvis_say(f"Done, sir. I added the quest: {add_titles[0]}.")
            else:
                self._jarvis_say(f"Done, sir. I added {len(add_titles)} quests.")
        elif completed:
            handled = True
            self.sfx.play("complete")
            self._set_pending_add(False)
            self._jarvis_say(f"Completed {completed} quest{'s' if completed != 1 else ''}, sir.")
        elif deleted:
            handled = True
            self.sfx.play("delete")
            self._set_pending_add(False)
            self._jarvis_say(f"Deleted {deleted} quest{'s' if deleted != 1 else ''}, sir.")
        elif self._pending_voice_add:
            handled = True
            self._jarvis_say("Of course. Tell me the quest.")
        elif replies:
            handled = True
            self._jarvis_say(" ".join(replies[:2]))

        if not handled:
            return False

        if not self._pending_voice_add:
            self._set_footer_default()
        return True

    def _apply_nonquest_llm_action(self, action: LLMAction) -> bool:
        if action.kind == "show":
            self._jarvis_say("Yes, sir.")
            self.state.jarvis_awake = True
            self._sync_voice_gate()
            if self.isVisible() or self._show_pending:
                return True
            if not self._should_accept_control_action("show"):
                return True
            self._show_pending = True
            self._after_speech_begins(self._finish_show, delay_ms=700)
            self._set_pending_add(False)
            return True

        if action.kind == "hide":
            self._jarvis_say("Going quiet, sir.")
            if not self.isVisible() or self._hide_pending:
                return True
            if not self._should_accept_control_action("hide"):
                return True
            self._hide_pending = True
            self._after_speech_begins(self._finish_hide, delay_ms=800)
            self._set_pending_add(False)
            return True

        if action.kind == "listen_off":
            self._jarvis_say("Mic off, sir.")
            if not self.state.listening_requested:
                return True
            if not self._should_accept_control_action("listen_off"):
                return True
            self._after_speech_begins(lambda: (self.sfx.play("mute"), self.disable_listening()), delay_ms=800)
            self._set_pending_add(False)
            return True

        if action.kind == "add_done":
            self._set_pending_add(False)
            self._set_footer_default()
            self._jarvis_say("Understood.")
            return True

        if action.kind == "quit":
            if not parse_quit_intent(self._last_command_text or ""):
                return False
            if not self._should_accept_control_action("quit"):
                return True
            self._jarvis_say("Goodbye, sir.", on_done=self._schedule_quit_after_goodbye)
            return True

        if action.kind == "set_fx":
            enabled = (action.value or "").strip().lower() in {"on", "true", "enable", "enabled"}
            if self.state.visuals_enabled == enabled:
                self._jarvis_say("Effects are already on, sir." if enabled else "Effects are already off, sir.")
                return True
            self._jarvis_say("Effects on." if enabled else "Effects off.")
            # Enable visuals on the next event-loop tick so TTS can start first,
            # without stacking a long timer on top of speech playback.
            QtCore.QTimer.singleShot(0, lambda enabled=enabled: self._apply_fx_enabled(enabled))
            return True

        if action.kind == "open_browser":
            self._jarvis_say("Opening browser.")
            self._after_speech_begins(lambda: (webbrowser.open("https://www.google.com"), self.sfx.play("show")), delay_ms=700)
            return True

        if action.kind == "web_search":
            query = (action.value or "").strip()
            if query:
                url = "https://www.google.com/search?q=" + urllib.parse.quote_plus(query)
                self._jarvis_say("Searching.")
                self._after_speech_begins(lambda: (webbrowser.open(url), self.sfx.play("show")), delay_ms=700)
            else:
                self._jarvis_say("Opening browser.")
                self._after_speech_begins(lambda: (webbrowser.open("https://www.google.com"), self.sfx.play("show")), delay_ms=700)
            return True

        return False

    def _finish_show(self) -> None:
        self._show_pending = False
        self.show_and_raise()
        self.sfx.play("show")

    def _finish_hide(self) -> None:
        try:
            self._hide_pending = False
            self.state.jarvis_awake = False
            self.sfx.play("hide")
            self.hide()
            self._sync_voice_gate()
            self._sync_privacy_button()
        except Exception:
            self._hide_pending = False

    @staticmethod
    def _quit_application() -> None:
        app = QtWidgets.QApplication.instance()
        if app is not None:
            app.quit()

    def _schedule_quit_after_goodbye(self) -> None:
        QtCore.QMetaObject.invokeMethod(
            self,
            "_quit_after_goodbye",
            QtCore.Qt.ConnectionType.QueuedConnection,
        )

    @QtCore.Slot()
    def _quit_after_goodbye(self) -> None:
        # Small tail after playback so the last syllable is not clipped on exit.
        QtCore.QTimer.singleShot(500, self._quit_application)

    def _should_accept_control_action(self, kind: str) -> bool:
        now = time.monotonic()
        if self._last_control_kind == kind and (now - self._last_control_at) < self._CONTROL_COOLDOWN_S:
            return False
        self._last_control_kind = kind
        self._last_control_at = now
        return True

    def _is_duplicate_voice_text(self, text: str) -> bool:
        normalized = " ".join((text or "").lower().split())
        if not normalized:
            return True
        now = time.monotonic()
        if normalized == self._last_voice_text and (now - self._last_voice_at) < self._VOICE_DEDUP_S:
            return True
        self._last_voice_text = normalized
        self._last_voice_at = now
        return False

    def _sync_voice_gate(self) -> None:
        # While Jarvis is awake and the mic is on, listen without "Jarvis".
        require_wake = not (self.state.jarvis_awake and self.state.listening_requested)
        self.voice.set_wake_word_required(require_wake)
        if self.state.listening_requested:
            self._set_footer_default()

    @staticmethod
    def _after_speech_begins(callback: Callable[[], None], *, delay_ms: int = 300) -> None:
        QtCore.QTimer.singleShot(delay_ms, callback)

    def _set_pending_add(self, enabled: bool) -> None:
        self._pending_voice_add = enabled
        self._pending_voice_add_until = time.monotonic() + self._ADD_MODE_TIMEOUT_S if enabled else 0.0

    def _expire_pending_add_if_needed(self) -> None:
        if self._pending_voice_add and time.monotonic() > self._pending_voice_add_until:
            self._set_pending_add(False)
            self._set_footer_default()

    @staticmethod
    def _should_cancel_pending_add(text: str) -> bool:
        lower = (text or "").strip().lower()
        if not lower:
            return False
        if "?" in lower:
            return True
        question_starts = (
            "why ",
            "what ",
            "how ",
            "when ",
            "where ",
            "who ",
            "did you ",
            "do you ",
            "can you delete",
            "delete ",
            "remove ",
            "open ",
            "wake ",
            "show ",
            "hide ",
            "sleep ",
            "stop listening",
        )
        return lower.startswith(question_starts)

    @QtCore.Slot()
    def _toggle_listening(self) -> None:
        if self.state.listening_requested:
            self.disable_listening()
        else:
            self.enable_listening(announce=False)
            self._set_footer_default()

    def enable_listening(self, *, announce: bool = True) -> None:
        self.state.listening_requested = True
        self._voice_restart_at = time.monotonic()
        self.voice.restart()
        if announce:
            self.sfx.play("unmute")
            self._jarvis_say("Listening, sir.")
        self._sync_voice_gate()
        self._sync_privacy_button()

    def disable_listening(self) -> None:
        self.state.listening_requested = False
        self.voice.stop()
        self.voice.set_wake_word_required(True)
        self.sfx.play("mute")
        self.footer.setText("Privacy mode: microphone is off. Click the red button to listen again.")
        self._sync_privacy_button()

    def _sync_privacy_button(self) -> None:
        if self.state.listening_requested:
            self.privacy_button.hide()
            return
        if not self.privacy_button.isVisible():
            self.privacy_button.show_near_bottom_right()
        else:
            self.privacy_button.raise_()

    @staticmethod
    def _hide_from_taskbar(widget: QtWidgets.QWidget) -> None:
        _hide_widget_from_taskbar(widget)

    def _on_voice_text(self, text: str) -> None:
        # Callback happens from a background thread -> hop to UI thread.
        QtCore.QMetaObject.invokeMethod(
            self,
            "_on_voice_text_ui",
            QtCore.Qt.ConnectionType.QueuedConnection,
            QtCore.Q_ARG(str, text),
        )

    @QtCore.Slot(str)
    def _on_voice_text_ui(self, text: str) -> None:
        try:
            if time.monotonic() < self._voice_grace_until:
                return
            if not self._is_meaningful_voice_text(text):
                return
            if self._is_tts_echo(text):
                return
            if self._is_duplicate_voice_text(text):
                return
            self._apply_text(text, source="voice")
        except Exception:
            pass

    @classmethod
    def _is_meaningful_voice_text(cls, text: str) -> bool:
        cleaned = " ".join((text or "").lower().split())
        if len(cleaned) < 3:
            return False
        words = [w.strip(".,!?;:") for w in cleaned.split()]
        if not words:
            return False
        if len(words) == 1 and words[0] in cls._VOICE_FILLER_WORDS:
            return False
        if len(words) <= 2 and all(word in cls._VOICE_FILLER_WORDS for word in words):
            return False
        if len(words) <= 3 and all(word in cls._VOICE_FILLER_WORDS for word in words):
            return False
        return True

    def _on_speaker_state(self, speaking: bool) -> None:
        QtCore.QMetaObject.invokeMethod(
            self,
            "_on_speaker_state_ui",
            QtCore.Qt.ConnectionType.QueuedConnection,
            QtCore.Q_ARG(bool, speaking),
        )

    @QtCore.Slot(bool)
    def _on_speaker_state_ui(self, speaking: bool) -> None:
        self.voice.set_results_muted(speaking)
        if not speaking:
            self._voice_grace_until = time.monotonic() + self._VOICE_GRACE_AFTER_SPEECH_S

    def _jarvis_say(self, text: str, *, on_done: Optional[Callable[[], None]] = None) -> None:
        text = (text or "").strip()
        if not text:
            return
        self._spoke_this_turn = True
        self._register_spoken_phrase(text)
        self.speaker.say(text, on_done=on_done)

    def _register_spoken_phrase(self, text: str) -> None:
        norm = self._normalize_echo_text(text)
        if not norm:
            return
        now = time.monotonic()
        self._spoken_echo_guard.append((now, norm))
        cutoff = now - self._ECHO_GUARD_S
        self._spoken_echo_guard = [(ts, phrase) for ts, phrase in self._spoken_echo_guard if ts >= cutoff]

    @staticmethod
    def _normalize_echo_text(text: str) -> str:
        cleaned = re.sub(r"[^\w\s]", "", (text or "").lower())
        return " ".join(cleaned.split())

    def _is_tts_echo(self, text: str) -> bool:
        norm = self._normalize_echo_text(text)
        if not norm:
            return True
        now = time.monotonic()
        words = norm.split()
        for ts, spoken in self._spoken_echo_guard:
            if now - ts > self._ECHO_GUARD_S:
                continue
            if norm == spoken:
                return True
            spoken_words = spoken.split()
            # Only block short near-verbatim replays of Jarvis speech, not real
            # commands that happen to contain the same words ("turn effects on").
            if len(words) <= 5 and len(spoken_words) <= 6:
                if norm in spoken or spoken in norm:
                    return True
                overlap = set(words) & set(spoken_words)
                if len(words) >= 2 and len(overlap) >= len(words) - 1 and len(overlap) == len(spoken_words):
                    return True
        return False

    def _on_item_changed(self, item: QtWidgets.QListWidgetItem) -> None:
        task_id = int(item.data(QtCore.Qt.ItemDataRole.UserRole))
        if item.checkState() == QtCore.Qt.CheckState.Checked:
            self.db.set_status(task_id, "done")
            self.sfx.play("complete")
            self.refresh()

    def _delete_selected_from(self, task_list: QtWidgets.QListWidget) -> None:
        items = task_list.selectedItems()
        if not items:
            self._jarvis_say("No quest selected.")
            return

        titles = [self._item_quest_title(item.text()) for item in items]
        for item in items:
            task_id = int(item.data(QtCore.Qt.ItemDataRole.UserRole))
            self.db.delete_task(task_id)

        if len(titles) == 1:
            self.sfx.play("delete")
            self._jarvis_say(f"Quest deleted: {titles[0]}.")
        else:
            self.sfx.play("delete")
            self._jarvis_say(f"Deleted {len(titles)} quests, sir.")
        self.refresh()

    @staticmethod
    def _item_quest_title(label: str) -> str:
        return re.sub(r"^\d+\.\s*", "", (label or "").strip())

    @staticmethod
    def _size_hint_for_title(title: str) -> QtCore.QSize:
        # Give long quest titles enough vertical room to wrap inside the widget.
        approx_chars_per_line = 42
        lines = max(1, (len(title) // approx_chars_per_line) + 1)
        return QtCore.QSize(0, min(92, 24 + (lines * 18)))

    # Simple drag-to-move for a frameless widget.
    def mousePressEvent(self, event: QtGui.QMouseEvent) -> None:  # noqa: N802
        if event.button() == QtCore.Qt.MouseButton.LeftButton:
            self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event: QtGui.QMouseEvent) -> None:  # noqa: N802
        if self._drag_pos is not None and event.buttons() & QtCore.Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_pos)
            event.accept()

    def mouseReleaseEvent(self, event: QtGui.QMouseEvent) -> None:  # noqa: N802
        self._drag_pos = None
        event.accept()

    def show_and_raise(self) -> None:
        self.show()
        self.setWindowState(self.windowState() & ~QtCore.Qt.WindowState.WindowMinimized)
        self.raise_()
        self.activateWindow()
        self._hide_from_taskbar(self)
        if self.state.listening_requested and not self.voice.is_running:
            self.voice.restart()
        self._sync_voice_gate()
        self._sync_privacy_button()

    def showEvent(self, event: QtGui.QShowEvent) -> None:  # noqa: N802
        self.state.jarvis_awake = True
        super().showEvent(event)
        self._hide_from_taskbar(self)
        if self.state.listening_requested and not self.voice.is_running:
            self.voice.restart()
        self._sync_voice_gate()
        self._sync_privacy_button()

    def hideEvent(self, event: QtGui.QHideEvent) -> None:  # noqa: N802
        self.state.jarvis_awake = False
        super().hideEvent(event)
        self._sync_voice_gate()
        self._sync_privacy_button()

    def _set_footer_default(self) -> None:
        self.footer.setText(self._default_footer_text())

    def _default_footer_text(self) -> str:
        if not self.state.listening_requested:
            return "Privacy mode: microphone is off. Click the red button to listen again."
        if self.state.jarvis_awake:
            if self.brain.is_enabled:
                return "Jarvis is awake. Just talk naturally — no exact phrases needed."
            return "Jarvis is awake. Talk naturally; Ollama improves understanding when running."
        if self.brain.is_enabled:
            return 'Say "Jarvis wake up", then talk naturally.'
        return 'Say "Jarvis wake up", or install Ollama for natural speech.'

