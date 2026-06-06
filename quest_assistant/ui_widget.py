from __future__ import annotations

import math
import os
import random
import re
import sys
import threading
import time
import urllib.parse
from dataclasses import dataclass
from typing import Callable, Optional

from PySide6 import QtCore, QtGui, QtWidgets

from quest_assistant.compose.models import ComposeRequest
from quest_assistant.db import QuestDB, Task
from quest_assistant.diagnostics import log_diagnostic
from quest_assistant.core.session import SessionContext
from quest_assistant.core.types import RouteKind, ToolCall
from quest_assistant.events import AssistantEvent, EventBus, EventMonitorService
from quest_assistant.intent import tools as intent_tools
from quest_assistant.planner.models import ResearchPlan
from quest_assistant.permissions import PermissionSession
from quest_assistant.intent.router import IntentRouter
from quest_assistant.local_llm import LLMAction, LLMResult, LocalLLMInterpreter
from quest_assistant.memory import MemoryStore, try_ingest_rememberance
from quest_assistant.memory.detect import looks_like_memory_panel_intent, resolve_memory_intent
from quest_assistant.monitor.logger import log_intent, log_voice
from quest_assistant.parser import (
    extract_add_titles,
    extract_delete_quest_number,
    extract_quest_titles,
    has_add_intent,
    has_complete_intent,
    has_delete_intent,
    has_edit_intent,
    has_numbered_quest_markers,
    infer_casual_intent,
    is_delete_title_placeholder,
    local_chat_reply,
    looks_like_chat,
    looks_like_delete_intent,
    looks_like_typed_quest,
    normalize_quest_title,
    normalize_voice_command,
    parse_action,
    parse_quest_number,
    parse_fx_enabled,
    parse_background_sleep_intent,
    parse_hide_intent,
    parse_listen_off_intent,
    looks_like_listen_off,
    parse_open_browser_intent,
    parse_open_target,
    parse_download_search_query,
    parse_quit_intent,
    parse_show_intent,
    parse_web_search_query,
    resolve_fx_enabled,
    split_into_items,
)
from quest_assistant.system.launcher import (
    build_search_url,
    canonical_open_target,
    launch_app,
    open_in_browser,
    open_url_or_site,
    resolve_browser_destination,
    resolve_site_url,
)
from quest_assistant.sound_effects import SoundEffects
from quest_assistant.speaker import Speaker
from quest_assistant.ui.action_host import QuestActionHost
from quest_assistant.ui.memory_panel import MemorySidePanel
from quest_assistant.ui.permission_dialog import PermissionConfirmDialog
from quest_assistant.voice.listener import (
    VoiceListener,
    _BACKGROUND_COMMAND_WINDOW_S,
    _DEFAULT_COMMAND_WINDOW_S,
)


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


@dataclass(frozen=True)
class _QuestListSnapshot:
    open_rows: list[tuple[int, int, str]]  # task_id, number, title
    done_rows: list[tuple[int, str]]  # task_id, title
    use_compact: bool


_ROLE_TASK_ID = QtCore.Qt.ItemDataRole.UserRole
_ROLE_QUEST_NUM = QtCore.Qt.ItemDataRole.UserRole + 1


class _OpenQuestListDelegate(QtWidgets.QStyledItemDelegate):
    """Paint 'N. title' but edit the plain title only — avoids double-text overlap."""

    _EDITOR_STYLE = (
        "QLineEdit {"
        "  background: rgb(15, 23, 42);"
        "  color: #f3f4f6;"
        "  border: 1px solid rgba(34, 211, 238, 90);"
        "  border-radius: 4px;"
        "  padding: 1px 4px;"
        "  selection-background-color: rgba(34, 211, 238, 120);"
        "}"
    )

    def paint(
        self,
        painter: QtGui.QPainter,
        option: QtWidgets.QStyleOptionViewItem,
        index: QtCore.QModelIndex,
    ) -> None:
        opt = QtWidgets.QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        widget = option.widget
        if widget is None:
            return

        if opt.state & QtWidgets.QStyle.StateFlag.State_Editing:
            # Erase the painted label so only the line editor is visible.
            opt.text = ""
        else:
            number = index.data(_ROLE_QUEST_NUM) or (index.row() + 1)
            title = index.data(QtCore.Qt.ItemDataRole.DisplayRole) or ""
            opt.text = f"{number}. {title}"

        widget.style().drawControl(
            QtWidgets.QStyle.ControlElement.CE_ItemViewItem,
            opt,
            painter,
            widget,
        )

    def createEditor(
        self,
        parent: QtWidgets.QWidget,
        option: QtWidgets.QStyleOptionViewItem,
        index: QtCore.QModelIndex,
    ) -> QtWidgets.QWidget:
        editor = QtWidgets.QLineEdit(parent)
        editor.setFrame(False)
        editor.setStyleSheet(self._EDITOR_STYLE)
        return editor

    def updateEditorGeometry(
        self,
        editor: QtWidgets.QWidget,
        option: QtWidgets.QStyleOptionViewItem,
        index: QtCore.QModelIndex,
    ) -> None:
        opt = QtWidgets.QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        widget = option.widget
        style = widget.style() if widget else QtWidgets.QApplication.style()
        text_rect = style.subElementRect(
            QtWidgets.QStyle.SubElement.SE_ItemViewItemText,
            opt,
            widget,
        )
        editor.setGeometry(text_rect)

    def setEditorData(self, editor: QtWidgets.QWidget, index: QtCore.QModelIndex) -> None:
        if isinstance(editor, QtWidgets.QLineEdit):
            editor.setText(index.data(QtCore.Qt.ItemDataRole.DisplayRole) or "")

    def setModelData(
        self,
        editor: QtWidgets.QWidget,
        model: QtCore.QAbstractItemModel,
        index: QtCore.QModelIndex,
    ) -> None:
        if isinstance(editor, QtWidgets.QLineEdit):
            model.setData(index, editor.text().strip(), QtCore.Qt.ItemDataRole.EditRole)


class PrivacyListenButton(QtWidgets.QWidget):
    _DIAMETER = round(168 * 0.88)  # 12% smaller than original 168px circle

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
    _MAX_SIGNALS = 3
    _SPAWN_CHANCE = 0.044  # tuned for ~20 FPS
    _SIGNAL_SPEED = (0.0125, 0.0238)
    _PALETTE = (
        (34, 211, 238),
        (56, 189, 248),
        (96, 165, 250),
        (45, 212, 191),
        (74, 222, 128),
        (167, 139, 250),
    )

    def __init__(self) -> None:
        super().__init__()
        self.enabled = False
        self._motion = False
        self.phase = 0.0
        self._rng = random.Random()
        self._signals: list[dict] = []
        self._paint_failures = 0
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_OpaquePaintEvent, False)

    def set_visuals(self, enabled: bool) -> None:
        self.enabled = enabled
        if not enabled:
            self._signals.clear()
            self._paint_failures = 0
            self._motion = False
        self._request_update()

    def set_motion(self, enabled: bool) -> None:
        self._motion = enabled
        if not enabled:
            self._signals.clear()
        self._request_update()

    def set_phase(self, phase: float) -> None:
        self.phase = phase
        if not self.enabled:
            return
        try:
            if self._motion:
                self._advance_signals()
            self._request_update()
        except Exception:
            self._signals.clear()

    def _request_update(self) -> None:
        if not self.enabled or not self.isVisible():
            return
        if self.width() <= 0 or self.height() <= 0:
            return
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
            painter = QtGui.QPainter(self)
            if not painter.isActive():
                return

            pulse = 0.55 + (0.45 * math.sin(self.phase))
            if not self._motion:
                pulse = 0.55 + (0.45 * math.sin(time.monotonic() * 1.6))

            glow = QtGui.QRadialGradient(QtCore.QPointF(width * 0.18, height * 0.12), width * 0.78)
            glow.setColorAt(0.0, QtGui.QColor(34, 211, 238, int(34 + (pulse * 22))))
            glow.setColorAt(0.42, QtGui.QColor(59, 130, 246, int(14 + (pulse * 12))))
            glow.setColorAt(1.0, QtGui.QColor(0, 0, 0, 0))
            painter.fillRect(self.rect(), QtGui.QBrush(glow))

            corner = QtGui.QRadialGradient(QtCore.QPointF(width * 0.88, height * 0.08), width * 0.42)
            corner.setColorAt(0.0, QtGui.QColor(167, 139, 250, int(10 + (pulse * 14))))
            corner.setColorAt(1.0, QtGui.QColor(0, 0, 0, 0))
            painter.fillRect(self.rect(), QtGui.QBrush(corner))

            if self._motion:
                painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)
                for signal in self._signals:
                    self._draw_signal(painter, signal, width, height)
            painter.end()
            self._paint_failures = 0
        except Exception:
            self._paint_failures += 1
            if self._paint_failures >= 3:
                self.enabled = False
                self._signals.clear()
            return

    def _advance_signals(self) -> None:
        self._signals = [s for s in self._signals if s["progress"] - s["length"] <= 1.02]
        for signal in self._signals:
            signal["progress"] += signal["speed"]

        if len(self._signals) < self._MAX_SIGNALS and self._rng.random() < self._SPAWN_CHANCE:
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
            "speed": self._rng.uniform(*self._SIGNAL_SPEED),
            "progress": -self._rng.uniform(0.04, 0.14),
            "length": self._rng.uniform(0.038, 0.072),
            "rgb": self._rng.choice(self._PALETTE),
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

        start = max(0.0, tail)
        end = min(1.0, head)
        if end <= start:
            return

        r, g, b = signal["rgb"]
        flicker = 0.78 + (0.22 * math.sin((head * 18.0) + self.phase))

        aura = QtGui.QPen(QtGui.QColor(r, g, b, int(52 * flicker)), 7)
        aura.setCapStyle(QtCore.Qt.PenCapStyle.RoundCap)
        aura.setJoinStyle(QtCore.Qt.PenJoinStyle.RoundJoin)
        painter.setPen(aura)
        self._draw_path_slice(painter, points, total, start, end)

        core = QtGui.QPen(
            QtGui.QColor(min(255, r + 50), min(255, g + 50), min(255, b + 40), int(230 * flicker)),
            2,
        )
        core.setCapStyle(QtCore.Qt.PenCapStyle.RoundCap)
        core.setJoinStyle(QtCore.Qt.PenJoinStyle.RoundJoin)
        painter.setPen(core)
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
    _VOICE_QUEUE_MAX = 6
    _LLM_PENDING_MAX = 2
    _VISUAL_FPS = 20
    _VISUAL_TIMER_MS = 1000 // _VISUAL_FPS
    _VISUAL_PHASE_STEP = 0.085 * (_VISUAL_TIMER_MS / 80)  # same glow speed as old ~12 FPS
    _COMPACT_LIST_THRESHOLD = 12
    _BASE_CARD_WIDTH = 380
    _WINDOW_CHROME_W = 24
    _MEMORY_TAB_PROTRUSION = 36
    _MEMORY_TAB_WIDTH = 36
    _MEMORY_TAB_HEIGHT = 96
    _MEMORY_TAB_OVERLAP = 5
    _LLM_CONTROL_KINDS = frozenset(
        {
            "show",
            "hide",
            "listen_off",
            "add_done",
            "quit",
            "set_fx",
            "open_browser",
            "open_app",
            "open_url",
            "web_search",
            "download_search",
        }
    )

    _VOICE_GRACE_AFTER_SPEECH_S = 1.4
    _REFRESH_DEBOUNCE_MS = 80
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

    # Delivered from background worker threads -> queued onto the UI thread.
    _llm_result_ready = QtCore.Signal(int, str, str, object)
    _planner_result_ready = QtCore.Signal(int, object, object)
    _compose_result_ready = QtCore.Signal(int, object, object)
    _vision_result_ready = QtCore.Signal(int, str)
    _refresh_data_ready = QtCore.Signal(int, object)

    def __init__(self, db: QuestDB) -> None:
        super().__init__()
        self.db = db
        self.memory = MemoryStore(db.path)
        self._permission_session = PermissionSession()
        self.event_bus = EventBus()
        self._event_service = EventMonitorService(self.event_bus)
        self.event_bus.event_posted.connect(self._on_assistant_event)
        self.state = UIState()
        self._pending_voice_add = False
        self._pending_voice_add_until = 0.0
        self._pending_voice_delete = False
        self._pending_voice_delete_until = 0.0
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
        self._planner_busy = False
        self._compose_busy = False
        self._vision_busy = False
        self._planner_seq = 0
        self._compose_seq = 0
        self._vision_seq = 0
        self._events_started = False
        self._llm_seq = 0
        self._refresh_pending = False
        self._refresh_dirty = False
        self._refresh_worker_busy = False
        self._refresh_seq = 0
        self._refreshing_lists = False
        self._voice_restart_at = 0.0
        self._voice_start_pending = False
        self._jarvis_speaking = False
        self._voice_command_busy = False
        self._voice_command_queue: list[str] = []
        self._fx_llm_pause_depth = 0
        self._pending_llm_requests: list[tuple[str, str]] = []
        self._llm_active_seq = 0
        self._llm_result_ready.connect(self._on_llm_result)
        self._planner_result_ready.connect(self._on_planner_result)
        self._compose_result_ready.connect(self._on_compose_result)
        self._vision_result_ready.connect(self._on_vision_result)
        self._refresh_data_ready.connect(self._on_refresh_data_ready)

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
        self._router = IntentRouter()
        self._action_host = QuestActionHost(self)
        self._last_apply_source = "voice"
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
        self.voice = VoiceListener(
            on_text=self._on_voice_text,
            on_error=self._on_voice_error,
            wake_word="jarvis",
            command_window_s=30.0,
        )
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
        self._visual_timer.setInterval(self._VISUAL_TIMER_MS)
        self._visual_timer.setTimerType(QtCore.Qt.TimerType.PreciseTimer)

        # Always listening in the background (wake word only while hidden).
        self.enable_listening(announce=False)
        self._sync_voice_gate()
        self._sync_privacy_button()

    def _build_ui(self) -> None:
        outer = QtWidgets.QHBoxLayout(self)
        outer.setContentsMargins(12, 12, 12, 12)
        outer.setSpacing(0)

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
        outer.addWidget(self.card, 1)

        self.memory_panel = MemorySidePanel(
            lambda: self.memory,
            on_open_changed=self._on_memory_panel_open_changed,
            parent=self,
        )
        outer.addWidget(self.memory_panel)

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
        self.input.setPlaceholderText('Add or rename… e.g. "wash dishes" or "rename task 1 to …"')
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
        self.open_list.setWordWrap(False)
        self.open_list.setUniformItemSizes(False)
        self.open_list.setItemDelegate(_OpenQuestListDelegate(self.open_list))
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

        self.memory_tab = QtWidgets.QPushButton("M\nE\nM\nO\nR\nY", parent=self)
        self.memory_tab.setObjectName("memoryTab")
        self.memory_tab.setCheckable(True)
        self.memory_tab.setToolTip("Show what Jarvis remembers locally")
        self.memory_tab.setFixedSize(self._MEMORY_TAB_WIDTH, self._MEMORY_TAB_HEIGHT)
        tab_font = self.memory_tab.font()
        tab_font.setBold(True)
        tab_font.setWeight(QtGui.QFont.Weight.Bold)
        tab_font.setPointSize(11)
        self.memory_tab.setFont(tab_font)
        self.memory_tab.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        self.memory_tab.clicked.connect(self._on_memory_button_clicked)
        self.memory_tab.lower()
        self._apply_memory_tab_style()

        self._sync_window_width_for_memory(open=False, animate=False)
        self._apply_visual_core_style(0.0)
        self._position_memory_tab()

    def _position_memory_tab(self) -> None:
        if not hasattr(self, "memory_tab") or not hasattr(self, "card"):
            return
        card_rect = self.card.geometry()
        tab_w = self.memory_tab.width()
        tab_h = self.memory_tab.height()
        # Sit slightly under the card edge; letters are left-aligned in the tab.
        x = card_rect.right() - self._MEMORY_TAB_OVERLAP
        y = card_rect.top() + 36
        max_y = card_rect.bottom() - tab_h - 12
        if y > max_y:
            y = max(card_rect.top() + 12, max_y)
        self.memory_tab.move(x, y)
        self.memory_tab.lower()
        self.card.raise_()
        if hasattr(self, "memory_panel"):
            self.memory_panel.raise_()

    def resizeEvent(self, event: QtGui.QResizeEvent) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._position_memory_tab()

    def _on_memory_button_clicked(self) -> None:
        if self.memory_panel.is_open:
            self.hide_memory_panel()
        else:
            self.show_memory_panel(announce=False)

    def _on_memory_panel_open_changed(self, open: bool) -> None:
        self.memory_tab.setChecked(open)
        self._apply_widget_style()
        self._apply_memory_tab_style()
        self._sync_window_width_for_memory(open=open, animate=True)
        self._position_memory_tab()
        if not open:
            self._set_footer_default()

    def _sync_window_width_for_memory(self, *, open: bool, animate: bool) -> None:
        extra = MemorySidePanel.OPEN_WIDTH if open else 0
        tab_extra = self._MEMORY_TAB_PROTRUSION
        target_w = self._BASE_CARD_WIDTH + self._WINDOW_CHROME_W + extra + tab_extra
        target_h = self.height() if self.height() > 100 else 520
        if not animate:
            self.resize(target_w, target_h)
            return
        anim = QtCore.QPropertyAnimation(self, b"size", self)
        anim.setDuration(240)
        anim.setEasingCurve(QtCore.QEasingCurve.Type.OutCubic)
        anim.setStartValue(self.size())
        anim.setEndValue(QtCore.QSize(target_w, target_h))
        anim.start(QtCore.QAbstractAnimation.DeletionPolicy.DeleteWhenStopped)

    def show_memory_panel(self, *, announce: bool = False) -> None:
        self.memory_panel.set_open(True)
        if announce:
            self._jarvis_say("Here is what I remember, sir.")
        self.footer.setText("Memory panel open — prefs, facts, and recent activity.")

    def hide_memory_panel(self, *, announce: bool = False) -> None:
        was_open = self.memory_panel.is_open or self.memory_panel.maximumWidth() > 8
        self.memory_panel.set_open(False)
        self._sync_window_width_for_memory(open=False, animate=True)
        self._position_memory_tab()
        self.memory_tab.setChecked(False)
        self._apply_memory_tab_style()
        self._set_footer_default()
        if announce:
            self._jarvis_say(
                "Memory closed, sir." if was_open else "Memory is already closed, sir."
            )

    def _refresh_memory_panel_if_open(self) -> None:
        if self.memory_panel.is_open:
            self.memory_panel.refresh()

    def _tick(self) -> None:
        self._expire_pending_add_if_needed()
        running = self.voice.is_running
        starting = self.voice.is_starting
        voice_error = self.voice.last_error
        if self.state.listening_requested and not running:
            if voice_error and not starting:
                self.listen_btn.setText("Listening: ERROR")
                self.footer.setText(voice_error)
            else:
                self.listen_btn.setText("Listening: STARTING…")
            # Do not restart while the listener thread is still loading the model
            # or opening the microphone — that race was freezing/crashing the UI.
            if not starting and not self._voice_start_pending:
                now = time.monotonic()
                if now - self._voice_restart_at > 8.0:
                    self._voice_restart_at = now
                    self._begin_voice_listener()
        elif self.state.listening_requested:
            self.listen_btn.setText("Listening: ON")
            self.listen_btn.setChecked(True)
            if voice_error is None:
                footer = self.footer.text()
                if footer.startswith("Speech model") or footer.startswith("Microphone"):
                    self._set_footer_default()
        else:
            self.listen_btn.setText("Listening: OFF")
            self.listen_btn.setChecked(False)
        self._sync_privacy_button()

    def _fx_should_animate(self) -> bool:
        return (
            self.state.visuals_enabled
            and self.isVisible()
            and not self._jarvis_speaking
            and not self._llm_busy
            and not self._voice_command_busy
            and not self._refresh_worker_busy
            and self._fx_llm_pause_depth == 0
        )

    def _sync_fx_timer(self) -> None:
        if not self.state.visuals_enabled or not self.isVisible():
            self._visual_timer.stop()
            self.fx_background.set_visuals(False)
            return
        self.fx_background.set_visuals(True)
        motion = self._fx_should_animate()
        self.fx_background.set_motion(motion)
        if motion:
            if not self._visual_timer.isActive():
                self._visual_timer.start()
            return
        self._visual_timer.stop()
        # Static ambient glow still breathes while FX is on but motion is paused.
        self.fx_background.update()

    def _pause_fx_render(self) -> None:
        self._visual_timer.stop()

    def _resume_fx_render(self) -> None:
        self._sync_fx_timer()

    def _pause_fx_for_llm(self) -> None:
        self._fx_llm_pause_depth = min(self._fx_llm_pause_depth + 1, 8)
        if self._fx_llm_pause_depth == 1:
            self._pause_fx_render()

    def _resume_fx_for_llm(self) -> None:
        if self._fx_llm_pause_depth <= 0:
            return
        self._fx_llm_pause_depth -= 1
        if self._fx_llm_pause_depth == 0:
            self._sync_fx_timer()

    def _fx_is_visually_on(self) -> bool:
        return bool(
            self.state.visuals_enabled
            and self.fx_background.enabled
            and self.visuals_btn.isChecked()
        )

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
            else:
                self._visual_timer.stop()
                self._visual_phase = 0.0
                self.fx_background.set_phase(0.0)
                self._last_core_alpha = -1
                self._apply_visual_core_style(0.0)
            self._sync_fx_timer()
        except Exception:
            self._visual_timer.stop()
            self.state.visuals_enabled = False

    def _animate_visuals(self) -> None:
        if not self._fx_should_animate():
            self._visual_timer.stop()
            return
        try:
            self._visual_phase = (self._visual_phase + self._VISUAL_PHASE_STEP) % (math.pi * 2)
            self.fx_background.set_phase(self._visual_phase)
        except Exception:
            self._visual_timer.stop()
            if self.fx_background.enabled:
                self.state.visuals_enabled = False
                self.visuals_btn.blockSignals(True)
                self.visuals_btn.setChecked(False)
                self.visuals_btn.blockSignals(False)
                self.visuals_btn.setText("AI FX: OFF")
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
              {self._card_radius_css()};
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
        self._apply_memory_tab_style()

    def _apply_memory_tab_style(self) -> None:
        if not hasattr(self, "memory_tab"):
            return
        # One look for every state — opening the panel must not change colors or weight.
        self.memory_tab.setStyleSheet(
            """
            QPushButton#memoryTab {
              background: rgba(0, 0, 0, 252);
              color: #ffffff;
              border: 1px solid rgba(255, 255, 255, 55);
              border-left: 0px;
              border-top-right-radius: 9px;
              border-bottom-right-radius: 9px;
              border-top-left-radius: 0px;
              border-bottom-left-radius: 0px;
              font-size: 11px;
              font-weight: 800;
              padding: 6px 14px 6px 3px;
              line-height: 1.08;
            }
            QPushButton#memoryTab:checked {
              background: rgba(0, 0, 0, 252);
              color: #ffffff;
              font-weight: 800;
              border-color: rgba(125, 211, 252, 150);
            }
            QPushButton#memoryTab:hover {
              background: rgba(0, 0, 0, 252);
              color: #ffffff;
              font-weight: 800;
              border-color: rgba(125, 211, 252, 180);
            }
            QPushButton#memoryTab:pressed {
              background: rgba(0, 0, 0, 252);
              color: #ffffff;
              font-weight: 800;
            }
            """
        )
        tab_font = self.memory_tab.font()
        tab_font.setBold(True)
        tab_font.setWeight(QtGui.QFont.Weight.Bold)
        tab_font.setPointSize(11)
        self.memory_tab.setFont(tab_font)

    def _card_radius_css(self) -> str:
        memory_open = getattr(self, "memory_panel", None) is not None and self.memory_panel.is_open
        if memory_open:
            return (
                "border-top-left-radius: 14px;"
                " border-bottom-left-radius: 14px;"
                " border-top-right-radius: 0px;"
                " border-bottom-right-radius: 0px;"
            )
        return "border-radius: 14px;"

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
        if self._refresh_worker_busy or self._refresh_pending:
            return
        self._refresh_pending = True
        QtCore.QTimer.singleShot(self._REFRESH_DEBOUNCE_MS, self._start_refresh_worker)

    def _start_refresh_worker(self) -> None:
        self._refresh_pending = False
        if not self._refresh_dirty:
            return
        self._refresh_dirty = False
        self._refresh_seq += 1
        seq = self._refresh_seq
        self._refresh_worker_busy = True
        self._visual_timer.stop()
        db = self.db

        def worker() -> None:
            snapshot: Optional[_QuestListSnapshot] = None
            try:
                open_tasks = db.list_tasks(status="open")
                done_tasks = db.list_tasks(status="done")
                total = len(open_tasks) + len(done_tasks)
                use_compact = total > QuestWidget._COMPACT_LIST_THRESHOLD
                open_rows = [(t.id, index, t.title) for index, t in enumerate(open_tasks, start=1)]
                done_rows = [(t.id, t.title) for t in done_tasks]
                snapshot = _QuestListSnapshot(open_rows, done_rows, use_compact)
            except Exception as exc:
                log_diagnostic("ui", "refresh worker failed", exc=exc)
            try:
                self._refresh_data_ready.emit(seq, snapshot)
            except Exception:
                pass

        threading.Thread(target=worker, name="QuestRefresh", daemon=True).start()

    @QtCore.Slot(int, object)
    def _on_refresh_data_ready(self, seq: int, snapshot: object) -> None:
        if seq != self._refresh_seq:
            return
        self._refresh_worker_busy = False
        if isinstance(snapshot, _QuestListSnapshot):
            self._apply_refresh_snapshot(snapshot)
        if self._refresh_dirty:
            self.refresh()
        else:
            self._sync_fx_timer()

    def _apply_refresh_snapshot(self, snapshot: _QuestListSnapshot) -> None:
        self._refreshing_lists = True
        try:
            self.setUpdatesEnabled(False)
            self.open_list.blockSignals(True)
            self.done_list.blockSignals(True)

            self.open_list.setUniformItemSizes(snapshot.use_compact)
            self.open_list.clear()
            for task_id, number, title in snapshot.open_rows:
                item = QtWidgets.QListWidgetItem(title)
                item.setData(_ROLE_TASK_ID, task_id)
                item.setData(_ROLE_QUEST_NUM, number)
                item.setFlags(
                    item.flags()
                    | QtCore.Qt.ItemFlag.ItemIsUserCheckable
                    | QtCore.Qt.ItemFlag.ItemIsEditable
                )
                item.setCheckState(QtCore.Qt.CheckState.Unchecked)
                if not snapshot.use_compact:
                    item.setSizeHint(self._size_hint_for_title(f"{number}. {title}"))
                self.open_list.addItem(item)

            self.done_list.setUniformItemSizes(snapshot.use_compact)
            self.done_list.clear()
            for task_id, title in snapshot.done_rows:
                item = QtWidgets.QListWidgetItem(title)
                item.setData(QtCore.Qt.ItemDataRole.UserRole, task_id)
                item.setFlags(item.flags() & ~QtCore.Qt.ItemFlag.ItemIsEditable)
                if not snapshot.use_compact:
                    item.setSizeHint(self._size_hint_for_title(title))
                self.done_list.addItem(item)
        except Exception as exc:
            log_diagnostic("ui", "refresh apply failed", exc=exc)
        finally:
            self.open_list.blockSignals(False)
            self.done_list.blockSignals(False)
            self.setUpdatesEnabled(True)
            self._refreshing_lists = False

    def _refresh_now(self) -> None:
        """Legacy entry point — route through the async worker."""
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
            if source == "voice" and self._spoke_this_turn:
                self.voice.touch_command_window()
        except Exception as exc:
            log_diagnostic("ui", f"apply_text failed ({source}): {text!r}", exc=exc)
            self.footer.setText("Something went wrong. See ~/.quest_assistant/diagnostic.log")

    def _memory_context(self) -> str:
        try:
            return self.memory.format_for_llm(max_chars=900)
        except Exception as exc:
            log_diagnostic("memory", "format_for_llm failed", exc=exc)
            return ""

    def request_tool_confirmation(self, calls: list[ToolCall]) -> bool:
        """UI-thread confirmation for medium/high risk tools."""
        dlg = PermissionConfirmDialog(self, calls)
        return dlg.exec() == QtWidgets.QDialog.DialogCode.Accepted

    @QtCore.Slot(object)
    def _on_assistant_event(self, event: object) -> None:
        if not isinstance(event, AssistantEvent):
            return
        self.footer.setText(event.message[:200])
        proactive_kinds = frozenset({"battery", "timer", "download"})
        if event.speak and (
            self.state.jarvis_awake or event.kind in proactive_kinds
        ):
            self._jarvis_say(event.message)
        try:
            self.memory.record_interaction(f"event:{event.kind} {event.message[:100]}")
        except Exception:
            pass

    def _session_context(self, source: str) -> SessionContext:
        return SessionContext(
            source=source,
            jarvis_awake=self.state.jarvis_awake,
            listening_requested=self.state.listening_requested,
            pending_add=self._pending_voice_add,
            pending_delete=self._pending_voice_delete,
            open_quests=self._open_quests_for_llm(),
            fx_visually_on=self._fx_is_visually_on(),
            may_use_ollama=self.brain.may_use_ollama,
            llm_enabled=self.brain.is_enabled,
            last_command_text=self._last_command_text,
            voice_filler_words=self._VOICE_FILLER_WORDS,
        )

    def _apply_text_impl(self, text: str, source: str) -> None:
        self._spoke_this_turn = False
        self._last_command_text = text
        self._last_apply_source = source
        self._expire_pending_add_if_needed()
        self._expire_pending_delete_if_needed()
        if self._pending_voice_add and self._should_cancel_pending_add(text):
            self._set_pending_add(False)
            self._set_footer_default()

        if try_ingest_rememberance(self.memory, text):
            self._jarvis_say("Noted, sir.")
            self.memory.record_interaction(f"remembered: {text[:120]}")
            self._refresh_memory_panel_if_open()
            self._complete_instant_command()
            return

        try:
            self.memory.record_interaction(text[:200])
        except Exception:
            pass

        log_intent(f"route_start source={source} text={text[:80]!r}")
        if self._try_memory_command(text, source):
            return
        if parse_show_intent(text):
            self._action_host.execute(
                [intent_tools.tool(intent_tools.TOOL_SHOW)],
                route_path="show",
            )
            self._complete_instant_command()
            return
        if self._try_compose_command(text, source):
            return

        decision = self._router.route(text, self._session_context(source))

        if decision.kind == RouteKind.EXECUTE and decision.tool_calls:
            self._action_host.execute(decision.tool_calls, route_path=decision.route_path)
            if decision.instant:
                self._complete_instant_command()
            else:
                self.refresh()
            return

        if decision.kind == RouteKind.PLAN and decision.research_plan is not None:
            self._planner_seq += 1
            if self._dispatch_planner(decision.research_plan, self._planner_seq):
                return
            self.refresh()
            return

        if decision.kind == RouteKind.COMPOSE and decision.compose_request is not None:
            self._compose_seq += 1
            if self._dispatch_compose(decision.compose_request, self._compose_seq):
                return
            if source == "voice" and not self._spoke_this_turn:
                self._jarvis_say("I'm still working on the last draft, sir.")
            self.refresh()
            return

        if decision.kind == RouteKind.VISION:
            self._vision_seq += 1
            prompt = decision.vision_prompt or text
            if self._dispatch_vision(prompt, self._vision_seq):
                return
            self.refresh()
            return

        if decision.kind == RouteKind.LLM:
            if self._try_compose_command(text, source):
                return
            self._llm_seq += 1
            if self._dispatch_llm(text, source, self._llm_seq):
                return
            if self._try_apply_parser_control_action(text):
                self._complete_instant_command()
                return
            self.refresh()
            return

        if decision.kind == RouteKind.TYPED_HINT and decision.footer:
            self.footer.setText(decision.footer)
            if source == "voice" and not self._spoke_this_turn:
                if "Ollama" in (decision.footer or ""):
                    self._jarvis_say("I need Ollama running to draft that for you, sir.")
                else:
                    self._jarvis_say(decision.footer)
            return

        if decision.kind == RouteKind.CONVERSATION:
            if parse_show_intent(text):
                self._action_host.execute(
                    [intent_tools.tool(intent_tools.TOOL_SHOW)],
                    route_path="show",
                )
                self._complete_instant_command()
                return
            if self._try_voice_conversation_fallback(text, source):
                self.refresh()
                return

        if decision.kind == RouteKind.NOOP:
            if self._try_apply_parser_control_action(text):
                self._complete_instant_command()
                return
            if source == "voice" and not self._spoke_this_turn:
                if not self.isVisible() and self.state.listening_requested:
                    self._jarvis_say('Start with "Jarvis", then say your command, sir.')
                else:
                    self._jarvis_say("Sorry sir, I didn't catch that.")

        self.refresh()

    def _resolve_fx_for_voice(self, text: str) -> Optional[bool]:
        fx = resolve_fx_enabled(text)
        if fx is not None:
            return fx
        if not self._fx_is_visually_on() or looks_like_listen_off(text):
            return None
        raw = normalize_voice_command(text)
        if not raw:
            return None
        lower = raw.lower()
        if re.search(r"\b(?:turn|switch|shut)\s+(?:it|them|those)\s+off\b", lower):
            return False
        if re.search(r"\b(?:turn|switch)\s+off\b", lower) and re.search(
            r"\b(?:fx|effects?|visuals?|animations?|glow(?:ing)?|flashy|lights?)\b",
            lower,
        ):
            return False
        return None

    def _try_apply_fx_command(self, text: str) -> bool:
        fx = self._resolve_fx_for_voice(text)
        if fx is None:
            return False
        return self._apply_nonquest_llm_action(
            LLMAction(kind="set_fx", value="on" if fx else "off")
        )

    def _try_apply_quest_command(self, text: str, source: str) -> bool:
        action = parse_action(text, allow_implicit_add=False)
        if action.kind == "delete":
            if action.quest_number is not None:
                self._set_pending_delete(False)
                self._delete_open_quest(number=action.quest_number)
                return True
            if action.title and not is_delete_title_placeholder(action.title):
                self._set_pending_delete(False)
                self._delete_open_quest(title=action.title)
                return True
            number = extract_delete_quest_number(text)
            if number is not None:
                self._set_pending_delete(False)
                self._delete_open_quest(number=number)
                return True
            if looks_like_delete_intent(text):
                self._set_pending_add(False)
                self._set_pending_delete(True)
                self.footer.setText("Say the quest name or number to delete.")
                self._jarvis_say("Which quest should I delete, sir? Say the name or task number.")
                return True
            return False
        if action.kind == "complete" and (action.title is not None or action.quest_number is not None):
            self._complete_open_quest(title=action.title, number=action.quest_number)
            return True
        if action.kind == "edit" and action.value and (action.title is not None or action.quest_number is not None):
            self._edit_open_quest(new_title=action.value, title=action.title, number=action.quest_number)
            return True
        return False

    def _allow_implicit_add(self, text: str, source: str) -> bool:
        if looks_like_chat(text):
            return False
        if self._pending_voice_delete:
            return False
        if self._pending_voice_add:
            return True
        if has_add_intent(text):
            return True
        if source == "typed":
            return looks_like_typed_quest(text)
        return False

    def _apply_parser_actions(self, text: str, source: str) -> bool:
        matched_parser = False
        items = [text] if extract_add_titles(text) or self._pending_voice_add else split_into_items(text)
        implicit_add = self._allow_implicit_add(text, source)
        for item in items:
            action = parse_action(
                item,
                allow_implicit_add=implicit_add,
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
                    added = 0
                    for title in titles:
                        if self.db.add_task(
                            title,
                            due_iso=action.due_iso,
                            source=source,
                            raw_input=action.raw,
                        ) is not None:
                            added += 1
                    if not added:
                        continue
                    self.sfx.play("add")
                    if added == 1:
                        self._jarvis_say(f"Done, sir. I added the quest: {titles[0]}.")
                    else:
                        self._jarvis_say(f"Done, sir. I added {added} quests.")
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

            if action.kind == "edit" and action.value and (action.title or action.quest_number is not None):
                self._edit_open_quest(
                    new_title=action.value,
                    title=action.title,
                    number=action.quest_number,
                )
                continue

        return matched_parser or self._spoke_this_turn

    def _should_dispatch_llm_awake(self, text: str) -> bool:
        cleaned = " ".join((text or "").split())
        if len(cleaned) < 2:
            return False
        words = [w.strip(".,!?;:") for w in cleaned.lower().split()]
        if not words:
            return False
        if len(words) == 1 and words[0] in QuestWidget._VOICE_FILLER_WORDS:
            return False
        if self._pending_voice_add or has_add_intent(text):
            return False
        if self._pending_voice_delete:
            return False
        if looks_like_listen_off(text) or parse_quit_intent(text):
            return False
        if resolve_fx_enabled(text) is not None:
            return False
        if (
            has_delete_intent(text)
            or looks_like_delete_intent(text)
            or has_complete_intent(text)
            or has_edit_intent(text)
        ):
            return False
        return True

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
        if parse_hide_intent(text) or parse_quit_intent(text) or looks_like_listen_off(text):
            return False
        if parse_open_browser_intent(text) or parse_open_target(text) or parse_download_search_query(text) or parse_web_search_query(text):
            return False
        if has_add_intent(text):
            return False
        quick = parse_action(text, allow_implicit_add=False)
        if quick.kind in {
            "show",
            "hide",
            "listen_off",
            "add_done",
            "quit",
            "complete",
            "delete",
            "edit",
            "add",
            "open_browser",
            "open_app",
            "open_url",
            "web_search",
            "download_search",
        }:
            return False
        return True

    def _dispatch_llm(self, text: str, source: str, seq: int) -> bool:
        if self._llm_busy:
            if source == "voice":
                # Keep only the latest utterance — draining a backlog feels laggy.
                self._pending_llm_requests = [(text, source)]
            return False
        self._llm_busy = True
        self._llm_active_seq = seq
        self.footer.setText("Working on it, sir…")
        self._pause_fx_for_llm()
        pending_add = self._pending_voice_add
        jarvis_awake = self.state.jarvis_awake
        dispatch_seq = seq
        brain = self.brain
        db = self.db
        memory = self.memory
        memory_ctx = self._memory_context()

        def worker() -> None:
            result = None
            try:
                open_quests = [
                    {"number": index, "title": task.title}
                    for index, task in enumerate(db.list_tasks(status="open"), start=1)
                ]
                fx = resolve_fx_enabled(text)
                if parse_show_intent(text):
                    result = LLMResult(
                        actions=[LLMAction(kind="show")],
                        elapsed_s=0.0,
                        model=brain.model,
                    )
                else:
                    memory_intent = resolve_memory_intent(
                        text,
                        memory_panel_open=self._memory_panel_visible(),
                    )
                    if memory_intent:
                        result = LLMResult(
                            actions=[
                                LLMAction(
                                    kind=(
                                        "hide_memory"
                                        if memory_intent == "hide"
                                        else "show_memory"
                                    )
                                )
                            ],
                            elapsed_s=0.0,
                            model=brain.model,
                        )
                    elif fx is not None:
                        result = LLMResult(
                            actions=[LLMAction(kind="set_fx", value="on" if fx else "off")],
                            elapsed_s=0.0,
                            model=brain.model,
                        )
                    elif looks_like_delete_intent(text) or has_delete_intent(text):
                        result = None
                    elif source == "voice" and jarvis_awake:
                        result = brain.interpret_voice(
                            text,
                            pending_add=pending_add,
                            jarvis_awake=jarvis_awake,
                            open_quests=open_quests,
                            memory_context=memory_ctx or None,
                        )
                    else:
                        result = brain.interpret(
                            text,
                            pending_add=pending_add,
                            jarvis_awake=jarvis_awake,
                            open_quests=open_quests,
                            source=source,
                            memory_context=memory_ctx or None,
                        )
            except Exception as exc:
                log_diagnostic("llm", "interpret worker failed", exc=exc)
                result = None
            finally:
                try:
                    self._llm_result_ready.emit(dispatch_seq, text, source, result)
                except Exception:
                    pass

        threading.Thread(target=worker, name="LLMWorker", daemon=True).start()
        return True

    def _finish_llm_turn(self) -> None:
        self._llm_busy = False
        self._resume_fx_for_llm()
        self._sync_fx_timer()
        while self._pending_llm_requests:
            pending_text, pending_source = self._pending_llm_requests.pop(0)
            ctx = self._session_context(pending_source)
            decision = self._router.route(pending_text, ctx)
            if self.brain.may_use_ollama and decision.kind == RouteKind.LLM:
                self._llm_seq += 1
                self._dispatch_llm(pending_text, pending_source, self._llm_seq)
                return

    def _dispatch_planner(self, plan: ResearchPlan, seq: int) -> bool:
        if self._planner_busy:
            return False
        self._planner_busy = True
        self.footer.setText("Researching, sir…")
        self._pause_fx_for_llm()
        self._action_host.execute(
            [
                intent_tools.tool(
                    intent_tools.TOOL_WEB_SEARCH,
                    query=plan.query,
                )
            ],
            route_path="planner_search",
        )
        brain = self.brain
        memory_ctx = self._memory_context()

        def worker() -> None:
            pair = None
            try:
                pair = brain.summarize_research(plan.query, memory_context=memory_ctx or None)
            except Exception as exc:
                log_diagnostic("planner", "summarize failed", exc=exc)
            finally:
                try:
                    self._planner_result_ready.emit(seq, plan, pair)
                except Exception:
                    pass

        threading.Thread(target=worker, name="PlannerWorker", daemon=True).start()
        return True

    def _memory_panel_visible(self) -> bool:
        return self.memory_panel.is_open or self.memory_panel.maximumWidth() > 8

    def _try_memory_command(self, text: str, source: str) -> bool:
        intent = resolve_memory_intent(
            text,
            memory_panel_open=self._memory_panel_visible(),
        )
        if intent is None:
            return False
        tool = (
            intent_tools.TOOL_HIDE_MEMORY
            if intent == "hide"
            else intent_tools.TOOL_SHOW_MEMORY
        )
        self._action_host.execute([intent_tools.tool(tool)], route_path="memory")
        self._complete_instant_command()
        return True

    def _try_compose_command(self, text: str, source: str) -> bool:
        from quest_assistant.compose.detect import resolve_compose_request

        compose = resolve_compose_request(text)
        if compose is None:
            return False
        if self.brain.may_use_ollama:
            self._compose_seq += 1
            if self._dispatch_compose(compose, self._compose_seq):
                return True
            if source == "voice" and not self._spoke_this_turn:
                self._jarvis_say("I'm still working on the last draft, sir.")
            self.refresh()
            return True
        self.footer.setText("Drafting needs Ollama running locally. Start Ollama, then try again.")
        if source == "voice" and not self._spoke_this_turn:
            self._jarvis_say("I need Ollama running to draft that for you, sir.")
        return True

    def _dispatch_compose(self, request: ComposeRequest, seq: int) -> bool:
        if self._compose_busy:
            return False
        self._compose_busy = True
        dest_label = {"word": "Word", "outlook": "Outlook", "notepad": "Notepad"}.get(
            request.destination, "Notepad"
        )
        self.footer.setText(f"Drafting your {dest_label} document, sir…")
        if not self._spoke_this_turn:
            self._jarvis_say(f"Drafting that in {dest_label} now, sir.")
        self._pause_fx_for_llm()
        brain = self.brain
        memory_ctx = self._memory_context()

        def worker() -> None:
            result = None
            try:
                result = brain.compose_document(
                    request.topic,
                    destination=request.destination,
                    memory_context=memory_ctx or None,
                )
            except Exception as exc:
                log_diagnostic("compose", "draft failed", exc=exc)
            finally:
                try:
                    self._compose_result_ready.emit(seq, request, result)
                except Exception:
                    pass

        threading.Thread(target=worker, name="ComposeWorker", daemon=True).start()
        return True

    @QtCore.Slot(int, object, object)
    def _on_compose_result(self, seq: int, request: object, content: object) -> None:
        try:
            if seq != self._compose_seq or not isinstance(request, ComposeRequest):
                return
            if not content or not str(content).strip():
                self._jarvis_say(
                    "I couldn't draft that text, sir. Check that Ollama is running and try again."
                )
                return
            from quest_assistant.system.compose import deliver_compose

            ok, spoken = deliver_compose(request.destination, str(content).strip(), request.topic)
            self._jarvis_say(spoken)
            if ok:
                try:
                    self.memory.record_interaction(f"compose:{request.destination} {request.topic[:80]}")
                except Exception:
                    pass
        except Exception as exc:
            log_diagnostic("compose", "result handling failed", exc=exc)
            self._jarvis_say("Something went wrong while preparing your draft, sir.")
        finally:
            self._compose_busy = False
            self._resume_fx_for_llm()
            self._set_footer_default()
            self.refresh()

    @QtCore.Slot(int, object, object)
    def _on_planner_result(self, seq: int, plan: object, result: object) -> None:
        try:
            if seq != self._planner_seq or not isinstance(plan, ResearchPlan):
                return
            if result is None:
                self._jarvis_say(
                    "I opened a web search, sir, but I need the local model to summarize and add a quest."
                )
                return
            summary, title = result
            if plan.add_quest and title:
                self._action_host.execute(
                    [
                        intent_tools.tool(
                            intent_tools.TOOL_CREATE_TASK,
                            titles=[title],
                            source=self._last_apply_source,
                        )
                    ],
                    route_path="planner_quest",
                )
            short = summary if len(summary) <= 220 else summary[:217] + "..."
            if plan.add_quest and title:
                self._jarvis_say(f"Sir, {short} I added the quest: {title}.")
            else:
                self._jarvis_say(f"Sir, {short}")
            try:
                self.memory.record_interaction(f"planner: {plan.query[:80]}")
            except Exception:
                pass
        except Exception as exc:
            log_diagnostic("planner", "result handling failed", exc=exc)
        finally:
            self._planner_busy = False
            self._resume_fx_for_llm()
            self._set_footer_default()
            self.refresh()

    def _dispatch_vision(self, prompt: str, seq: int) -> bool:
        if self._vision_busy:
            return False
        from quest_assistant.vision.capture import capture_primary_screen
        from quest_assistant.vision.describe import describe_screenshot
        from quest_assistant.vision.detect import vision_enabled

        if not vision_enabled():
            self._jarvis_say(
                "Screen vision is off. Set JARVIS_VISION=1 and pull a vision model in Ollama, sir."
            )
            return False
        self._vision_busy = True
        self.footer.setText("Looking at your screen…")
        path = capture_primary_screen()
        if path is None:
            self._vision_busy = False
            self._jarvis_say("I could not capture the screen, sir.")
            return False

        def worker() -> None:
            reply = ""
            try:
                described = describe_screenshot(path, user_prompt=prompt)
                reply = described or ""
            except Exception as exc:
                log_diagnostic("vision", "describe failed", exc=exc)
            finally:
                try:
                    self._vision_result_ready.emit(seq, reply)
                except Exception:
                    pass

        threading.Thread(target=worker, name="VisionWorker", daemon=True).start()
        return True

    @QtCore.Slot(int, str)
    def _on_vision_result(self, seq: int, reply: str) -> None:
        try:
            if seq != self._vision_seq:
                return
            if reply.strip():
                self._jarvis_say(reply.strip()[:450])
            else:
                self._jarvis_say(
                    "I could not describe the screen. Install a vision model in Ollama, for example llava, sir."
                )
        except Exception as exc:
            log_diagnostic("vision", "result handling failed", exc=exc)
        finally:
            self._vision_busy = False
            self._set_footer_default()
            self.refresh()

    @QtCore.Slot(int, str, str, object)
    def _on_llm_result(self, seq: int, text: str, source: str, result: object) -> None:
        try:
            if seq == self._llm_seq:
                self._spoke_this_turn = False
                self._last_command_text = text
                self._last_apply_source = source
                ctx = self._session_context(source)

                from quest_assistant.intent import parser_route

                fx_calls = parser_route.route_fx_voice(text, fx_visually_on=ctx.fx_visually_on)
                if fx_calls:
                    self._action_host.execute(fx_calls, route_path="llm_fx")
                    self._set_footer_default()
                    self.refresh()
                    return

                quest_calls = parser_route.route_quest_command(text)
                if quest_calls:
                    self._action_host.execute(quest_calls, route_path="llm_quest")
                    self._set_footer_default()
                    self.refresh()
                    return

                if ctx.pending_add:
                    add_calls = parser_route.route_parser_actions(text, ctx)
                    if add_calls:
                        self._action_host.execute(add_calls, route_path="llm_pending_add")
                        self._set_footer_default()
                        self.refresh()
                        return

                from quest_assistant.compose.detect import is_echo_reply, resolve_compose_request

                mem_calls = parser_route.route_memory_controls(text)
                if mem_calls:
                    self._action_host.execute(mem_calls, route_path="llm_memory")
                    self._set_footer_default()
                    self.refresh()
                    return

                compose = resolve_compose_request(text)
                if compose and self.brain.may_use_ollama:
                    self._compose_seq += 1
                    if self._dispatch_compose(compose, self._compose_seq):
                        return

                if result is not None:
                    result_actions = getattr(result, "actions", None) or []
                    actions = [a for a in result_actions if a.kind != "noop"]
                    if actions and self._action_host.execute_llm_actions(
                        actions, utterance=text, source=source
                    ):
                        self._set_footer_default()
                        self.refresh()
                        return
                    replies = [a.value for a in result_actions if a.kind == "reply" and a.value]
                    if replies and self.state.jarvis_awake and source == "voice":
                        spoken = " ".join(replies[:2])
                        if is_echo_reply(text, spoken):
                            if compose and self.brain.may_use_ollama:
                                self._compose_seq += 1
                                if self._dispatch_compose(compose, self._compose_seq):
                                    return
                            self._jarvis_say(
                                "I'll draft that for you once Ollama is ready, sir."
                            )
                            return
                        self._jarvis_say(spoken)
                        self._set_footer_default()
                        self.refresh()
                        return

                parser_calls = parser_route.route_parser_actions(text, ctx)
                if parser_calls:
                    self._action_host.execute(parser_calls, route_path="llm_parser_fallback")
                    self._set_footer_default()
                    self.refresh()
                    return

                if self._try_voice_conversation_fallback(text, source):
                    self._set_footer_default()
                    self.refresh()
                    return
                self._set_footer_default()
                self.refresh()
        except Exception as exc:
            log_diagnostic("ui", "llm result handling failed", exc=exc)
        finally:
            if seq == self._llm_active_seq:
                self._finish_llm_turn()
                if not self._llm_busy:
                    self._set_footer_default()

    def _complete_instant_command(self) -> None:
        """Parser handled the utterance immediately — clear any stale LLM wait state."""
        if self._llm_busy:
            self._llm_seq += 1
        self._refresh_memory_panel_if_open()
        self._set_footer_default()
        self.refresh()

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
            return self.db.find_best_open_by_title(title)
        return None

    def _open_task_ids_for_batch_targets(
        self,
        *,
        numbers: list[int],
        titles: list[str],
    ) -> set[int]:
        """
        Resolve 1-based open-task numbers and title needles against a single snapshot
        so multi-delete/complete by number does not shift indices mid-batch.
        """
        open_tasks = self.db.list_tasks(status="open")
        ids: set[int] = set()
        for number in numbers:
            if number is not None and 1 <= number <= len(open_tasks):
                ids.add(open_tasks[number - 1].id)
        for title in titles:
            if not title:
                continue
            task = self.db.find_best_open_by_title(title)
            if task is not None:
                ids.add(task.id)
        return ids

    def _complete_open_quest(
        self,
        *,
        title: Optional[str] = None,
        number: Optional[int] = None,
    ) -> bool:
        task = self._resolve_open_quest(title=title, number=number)
        if task:
            self.db.set_status(task.id, "done")
            self.sfx.play("complete")
            if number is not None:
                self._jarvis_say(f"Task {number} completed: {task.title}.")
            else:
                self._jarvis_say(f"Quest completed: {task.title}.")
            self._finish_quest_mutation()
            return True
        self.sfx.play("error")
        if number is not None:
            self._jarvis_say(f"I could not find open task {number}, sir.")
        else:
            self._jarvis_say(f"I could not find an open quest matching {title}.")
        self._finish_quest_mutation()
        return False

    def _delete_open_quest(
        self,
        *,
        title: Optional[str] = None,
        number: Optional[int] = None,
    ) -> bool:
        task: Optional[Task] = None
        if number is not None:
            task = self.db.get_open_task_by_number(number)
        elif title:
            task = self.db.find_best_open_by_title(title)

        if task:
            self.db.delete_task(task.id)
            self.sfx.play("delete")
            if number is not None:
                self._jarvis_say(f"Task {number} deleted: {task.title}.")
            else:
                self._jarvis_say(f"Quest deleted: {task.title}.")
            self._finish_quest_mutation()
            return True
        self.sfx.play("error")
        if number is not None:
            self._jarvis_say(f"I could not find open task {number}, sir.")
        else:
            self._jarvis_say(f"I could not find an open quest matching {title}.")
        self._finish_quest_mutation()
        return False

    def _edit_open_quest(
        self,
        *,
        new_title: str,
        title: Optional[str] = None,
        number: Optional[int] = None,
    ) -> None:
        cleaned = normalize_quest_title(new_title)
        if not cleaned:
            self.sfx.play("error")
            self._jarvis_say("I need a new quest title, sir.")
            return

        task = self._resolve_open_quest(title=title, number=number)
        if not task:
            self.sfx.play("error")
            if number is not None:
                self._jarvis_say(f"I could not find open task {number}, sir.")
            else:
                self._jarvis_say(f"I could not find an open quest matching {title}.")
            self._finish_quest_mutation()
            return

        if task.title == cleaned:
            self._jarvis_say("That quest already has that title, sir.")
            self._finish_quest_mutation()
            return

        if not self.db.update_task_title(task.id, cleaned):
            self.sfx.play("error")
            self._jarvis_say("I could not update that quest, sir.")
            self._finish_quest_mutation()
            return

        self.sfx.play("add")
        if number is not None:
            self._jarvis_say(f"Task {number} updated to {cleaned}.")
        else:
            self._jarvis_say(f"Quest updated to {cleaned}.")
        self._finish_quest_mutation()

    def _try_apply_parser_control_action(self, text: str) -> bool:
        if looks_like_listen_off(text):
            return self._apply_nonquest_llm_action(LLMAction(kind="listen_off"))

        if parse_hide_intent(text):
            if self._memory_panel_visible():
                mem = resolve_memory_intent(text, memory_panel_open=True)
                if mem == "hide":
                    return self._try_memory_command(text, self._last_apply_source or "voice")
            return self._apply_nonquest_llm_action(LLMAction(kind="hide"))

        if parse_quit_intent(text):
            return self._apply_nonquest_llm_action(LLMAction(kind="quit"))

        open_target = parse_open_target(text)
        if open_target is not None:
            kind, target = open_target
            if kind == "url":
                return self._apply_nonquest_llm_action(LLMAction(kind="open_url", value=target))
            return self._apply_nonquest_llm_action(LLMAction(kind="open_app", value=target))

        download_query = parse_download_search_query(text)
        if download_query:
            return self._apply_nonquest_llm_action(LLMAction(kind="download_search", value=download_query))

        if parse_open_browser_intent(text):
            return self._apply_nonquest_llm_action(LLMAction(kind="open_browser"))

        query = parse_web_search_query(text)
        if query:
            return self._apply_nonquest_llm_action(LLMAction(kind="web_search", value=query))

        action = parse_action(text, allow_implicit_add=False)
        if action.kind not in {
            "show",
            "hide",
            "listen_off",
            "add_done",
            "quit",
            "open_browser",
            "open_app",
            "open_url",
            "web_search",
            "download_search",
        }:
            return False
        if action.kind == "web_search":
            return self._apply_nonquest_llm_action(LLMAction(kind="web_search", value=action.value))
        if action.kind == "download_search":
            return self._apply_nonquest_llm_action(LLMAction(kind="download_search", value=action.value))
        if action.kind == "open_browser":
            return self._apply_nonquest_llm_action(LLMAction(kind="open_browser"))
        if action.kind == "open_app":
            return self._apply_nonquest_llm_action(LLMAction(kind="open_app", value=action.value))
        if action.kind == "open_url":
            return self._apply_nonquest_llm_action(LLMAction(kind="open_url", value=action.value))
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
            return self._try_apply_fx_command(text)
        if action.kind == "quit":
            return parse_quit_intent(text) and self._apply_nonquest_llm_action(LLMAction(kind="quit"))
        if action.kind in {"show", "hide", "listen_off"}:
            if action.kind == "listen_off" and not looks_like_listen_off(text):
                return False
            return self._apply_nonquest_llm_action(LLMAction(kind=action.kind))
        return False

    def _try_voice_conversation_fallback(self, text: str, source: str) -> bool:
        if source != "voice" or self._spoke_this_turn:
            return False
        if not self.state.jarvis_awake or not self.state.listening_requested:
            return False

        if looks_like_listen_off(text):
            return self._apply_nonquest_llm_action(LLMAction(kind="listen_off"))

        cleaned = " ".join((text or "").split())
        if len(cleaned) < 2:
            return False

        offline = local_chat_reply(text)
        if offline:
            self._jarvis_say(offline)
            return True

        if looks_like_chat(text):
            if self._try_compose_command(text, source):
                return True
            if self.brain.may_use_ollama:
                self._llm_seq += 1
                if self._dispatch_llm(text, source, self._llm_seq):
                    return True
            self._jarvis_say(
                "I can chat more freely when Ollama is running on this PC, sir."
            )
            return True

        if not self.brain.may_use_ollama:
            self._jarvis_say(
                "I can manage quests, effects, and searches when Ollama is running on this PC, sir."
            )
            return True

        return False

    def _apply_llm_result(self, text: str, source: str, result: object) -> bool:
        if self._try_apply_fx_command(text):
            return True
        delete_like = looks_like_delete_intent(text) or has_delete_intent(text)
        if delete_like and self._try_apply_quest_command(text, source):
            return True
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
                    from quest_assistant.compose.detect import is_echo_reply

                    spoken = " ".join(replies[:2])
                    if is_echo_reply(text, spoken):
                        if self._try_compose_command(text, source):
                            return True
                    else:
                        self._jarvis_say(spoken)
                    return True
            return False

        allow_add_actions = (
            not delete_like
            and not looks_like_delete_intent(text)
            and not has_delete_intent(text)
            and not has_complete_intent(text)
            and not has_edit_intent(text)
            and not looks_like_chat(text)
            and (
                self._pending_voice_add
                or has_add_intent(text)
                or bool(extract_add_titles(text))
            )
        )
        add_titles: list[str] = []
        completed = 0
        deleted = 0
        pending_complete_numbers: list[int] = []
        pending_complete_titles: list[str] = []
        pending_delete_numbers: list[int] = []
        pending_delete_titles: list[str] = []
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
            if action.kind == "listen_off" and not looks_like_listen_off(text):
                continue
            if self._apply_nonquest_llm_action(action):
                handled = True
                continue

            if action.kind == "add":
                if not allow_add_actions:
                    continue
                if delete_like:
                    number = extract_delete_quest_number(text)
                    if number is not None:
                        pending_delete_numbers.append(number)
                        handled = True
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
                if not has_complete_intent(text) and not (self.state.jarvis_awake and source == "voice"):
                    continue
                title = action.title
                number = self._quest_number_from_llm_title(title or "")
                if number is not None:
                    pending_complete_numbers.append(number)
                elif title:
                    pending_complete_titles.append(title)
                continue

            if action.kind == "delete":
                if not looks_like_delete_intent(text) and not has_delete_intent(text):
                    continue
                allow_multi = bool(
                    re.search(
                        r"\b(?:all|both|everything|each|every)\b|\band\b|\d+\s*(?:,|and)\s*\d+",
                        text,
                        re.IGNORECASE,
                    )
                )
                if (pending_delete_numbers or pending_delete_titles) and not allow_multi:
                    continue
                number = self._quest_number_from_llm_title(action.title or "")
                if number is not None:
                    pending_delete_numbers.append(number)
                elif action.title:
                    pending_delete_titles.append(action.title)
                continue

            if action.kind == "edit":
                if not action.value:
                    continue
                if not has_edit_intent(text) and not re.search(
                    r"\b(?:edit|rename|change|fix|correct|update)\b",
                    text,
                    re.IGNORECASE,
                ):
                    continue
                new_title = action.value
                if not new_title:
                    continue
                number = self._quest_number_from_llm_title(action.title or "")
                old_title = None if number is not None else action.title
                if number is not None or old_title:
                    task = self._resolve_open_quest(title=old_title, number=number)
                    updated = normalize_quest_title(new_title) or new_title
                    if task and self.db.update_task_title(task.id, updated):
                        handled = True
                        self.sfx.play("add")
                        if number is not None:
                            self._jarvis_say(f"Task {number} updated to {updated}.")
                        else:
                            self._jarvis_say(f"Quest updated to {updated}.")
                continue

            if action.kind == "reply" and action.value:
                replies.append(action.value)
                continue

        if pending_complete_numbers or pending_complete_titles:
            complete_ids = self._open_task_ids_for_batch_targets(
                numbers=pending_complete_numbers,
                titles=pending_complete_titles,
            )
            for task_id in complete_ids:
                self.db.set_status(task_id, "done")
            if complete_ids:
                completed = len(complete_ids)
                handled = True

        if pending_delete_numbers or pending_delete_titles:
            delete_ids = self._open_task_ids_for_batch_targets(
                numbers=pending_delete_numbers,
                titles=pending_delete_titles,
            )
            for task_id in delete_ids:
                self.db.delete_task(task_id)
            if delete_ids:
                deleted = len(delete_ids)
                handled = True

        added_count = 0
        for title in add_titles:
            if self.db.add_task(title, source=f"{source}:llm", raw_input=text) is not None:
                added_count += 1

        if added_count:
            handled = True
            self.sfx.play("add")
            self._set_pending_add(False)
            if added_count == 1:
                self._jarvis_say(f"Done, sir. I added the quest: {add_titles[0]}.")
            else:
                self._jarvis_say(f"Done, sir. I added {added_count} quests.")
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
            from quest_assistant.compose.detect import is_echo_reply, resolve_compose_request

            spoken = " ".join(replies[:2])
            if is_echo_reply(text, spoken):
                compose = resolve_compose_request(text)
                if compose and self.brain.may_use_ollama:
                    self._compose_seq += 1
                    if self._dispatch_compose(compose, self._compose_seq):
                        return True
                self._jarvis_say("I'll draft that for you once Ollama is ready, sir.")
                return True
            handled = True
            self._jarvis_say(spoken)

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
            self._set_pending_add(False)
            if not self.isVisible():
                self._show_pending = False
                self._finish_show()
            return True

        if action.kind == "hide":
            mem = resolve_memory_intent(
                self._last_command_text or "",
                memory_panel_open=self._memory_panel_visible(),
            )
            if mem == "hide":
                self.hide_memory_panel(announce=True)
                return True
            self._jarvis_say("Going quiet, sir.")
            self._pause_fx_render()
            if not self.isVisible() or self._hide_pending:
                return True
            if not self._should_accept_control_action("hide"):
                return True
            self._hide_pending = True
            deep_sleep = parse_background_sleep_intent(self._last_command_text or "")
            self._after_speech_begins(
                lambda: self._finish_hide(deep_sleep=deep_sleep),
                delay_ms=800,
            )
            self._set_pending_add(False)
            return True

        if action.kind == "listen_off":
            if not looks_like_listen_off(self._last_command_text or ""):
                return False
            if not self.state.listening_requested:
                self._jarvis_say("Mic is already off, sir.")
                return True
            self._jarvis_say("Mic off, sir.")
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
                return False
            self._jarvis_say("Goodbye, sir.", on_done=self._schedule_quit_after_goodbye)
            return True

        if action.kind == "set_fx":
            enabled = (action.value or "").strip().lower() in {"on", "true", "enable", "enabled"}
            if enabled and self._fx_is_visually_on():
                self._jarvis_say("Effects are already on, sir.")
                return True
            if not enabled and not self._fx_is_visually_on():
                self._jarvis_say("Effects are already off, sir.")
                return True
            self._set_visuals_enabled(enabled)
            self._jarvis_say("Effects on." if enabled else "Effects off.")
            return True

        if action.kind == "open_browser":
            target_url, spoken = resolve_browser_destination(action.value or "")
            self._jarvis_say(spoken)
            self._after_speech_begins(
                lambda u=target_url: (open_in_browser(u), self.sfx.play("show")),
                delay_ms=700,
            )
            return True

        if action.kind == "open_app":
            name = (action.value or action.title or "").strip()
            ok, spoken = launch_app(name)
            self._jarvis_say(spoken)
            if ok:
                self._after_speech_begins(lambda: self.sfx.play("show"), delay_ms=700)
            return True

        if action.kind == "open_url":
            target = canonical_open_target((action.value or "").strip()) or (action.value or "").strip()
            ok, spoken = open_url_or_site(target)
            self._jarvis_say(spoken)
            if ok:
                self._after_speech_begins(lambda: self.sfx.play("show"), delay_ms=700)
            return True

        if action.kind == "web_search":
            query = (action.value or "").strip()
            if query:
                url = build_search_url(query)
                self._jarvis_say("Searching.")
                self._after_speech_begins(
                    lambda u=url: (open_in_browser(u), self.sfx.play("show")),
                    delay_ms=700,
                )
            else:
                self._jarvis_say("Opening browser.")
                self._after_speech_begins(
                    lambda: (open_in_browser("https://www.google.com"), self.sfx.play("show")),
                    delay_ms=700,
                )
            return True

        if action.kind == "download_search":
            query = (action.value or "").strip()
            if query:
                url = build_search_url(query, download=True)
                self._jarvis_say(f"Searching for a download, sir.")
                self._after_speech_begins(
                    lambda u=url: (open_in_browser(u), self.sfx.play("show")),
                    delay_ms=700,
                )
            else:
                self._jarvis_say("What should I download, sir?")
            return True

        return False

    def _finish_show(self) -> None:
        self._show_pending = False
        self.show_and_raise()
        self.sfx.play("show")

    def _finish_hide(self, *, deep_sleep: bool = False) -> None:
        try:
            self._hide_pending = False
            if deep_sleep:
                self.state.jarvis_awake = False
            self._pause_fx_render()
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

    def _quit_assistance_confirmed(self) -> None:
        """Quit after the user approved the permission dialog (skip echo/cooldown guards)."""
        self._jarvis_say("Goodbye, sir.", on_done=self._schedule_quit_after_goodbye)

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
        if kind == "show" and not self.isVisible():
            self._last_control_kind = kind
            self._last_control_at = now
            return True
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
        listening = self.state.listening_requested
        visible = self.isVisible()
        awake = self.state.jarvis_awake
        # Visible + awake: talk naturally. Hidden: require "Jarvis" to start a session.
        require_wake = not (visible and awake and listening)
        background_mode = not visible and listening
        window_s = (
            _BACKGROUND_COMMAND_WINDOW_S if background_mode else _DEFAULT_COMMAND_WINDOW_S
        )
        self.voice.set_wake_gate_mode(
            require_wake_word=require_wake,
            background_mode=background_mode,
            command_window_s=window_s,
        )
        if self.state.listening_requested:
            self._set_footer_default()

    @staticmethod
    def _after_speech_begins(callback: Callable[[], None], *, delay_ms: int = 300) -> None:
        QtCore.QTimer.singleShot(delay_ms, callback)

    def _set_pending_add(self, enabled: bool) -> None:
        self._pending_voice_add = enabled
        self._pending_voice_add_until = time.monotonic() + self._ADD_MODE_TIMEOUT_S if enabled else 0.0
        if enabled:
            self._set_pending_delete(False)

    def _set_pending_delete(self, enabled: bool) -> None:
        self._pending_voice_delete = enabled
        self._pending_voice_delete_until = time.monotonic() + self._ADD_MODE_TIMEOUT_S if enabled else 0.0
        if enabled:
            self._set_pending_add(False)

    def _expire_pending_add_if_needed(self) -> None:
        if self._pending_voice_add and time.monotonic() > self._pending_voice_add_until:
            self._set_pending_add(False)
            self._set_footer_default()

    def _expire_pending_delete_if_needed(self) -> None:
        if self._pending_voice_delete and time.monotonic() > self._pending_voice_delete_until:
            self._set_pending_delete(False)
            self._set_footer_default()

    def _try_apply_pending_delete(self, text: str) -> bool:
        if not self._pending_voice_delete:
            return False
        if looks_like_listen_off(text) or parse_quit_intent(text):
            self._set_pending_delete(False)
            return False

        action = parse_action(text, allow_implicit_add=False)
        if action.kind == "delete":
            if action.quest_number is not None:
                self._set_pending_delete(False)
                self._delete_open_quest(number=action.quest_number)
                return True
            if action.title and not is_delete_title_placeholder(action.title):
                self._set_pending_delete(False)
                self._delete_open_quest(title=action.title)
                return True

        if has_add_intent(text):
            self._set_pending_delete(False)
            return False

        raw = normalize_voice_command(text)
        if not raw:
            return False

        words = raw.split()
        if len(words) == 1:
            number = parse_quest_number(words[0])
            if number is not None:
                self._set_pending_delete(False)
                self._delete_open_quest(number=number)
                return True

        title = normalize_quest_title(raw)
        if not title:
            return False
        self._set_pending_delete(False)
        self._delete_open_quest(title=title)
        return True

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
        self._voice_start_pending = announce
        self._sync_voice_gate()
        self._sync_privacy_button()
        if announce:
            self.sfx.play("unmute")
            # Open the mic only after TTS finishes so audio output and input
            # do not fight on Windows (that combination was crashing the widget).
            self._jarvis_say("Listening, sir.", on_done=self._schedule_voice_listener_start)
        else:
            self._begin_voice_listener()

    def _schedule_voice_listener_start(self) -> None:
        QtCore.QMetaObject.invokeMethod(
            self,
            "_begin_voice_listener",
            QtCore.Qt.ConnectionType.QueuedConnection,
        )

    @QtCore.Slot()
    def _begin_voice_listener(self) -> None:
        if not self.state.listening_requested:
            return
        self._voice_start_pending = False
        try:
            self._voice_restart_at = time.monotonic()
            self.voice.restart()
        except Exception:
            pass

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

    def _on_voice_error(self, message: str) -> None:
        QtCore.QMetaObject.invokeMethod(
            self,
            "_on_voice_error_ui",
            QtCore.Qt.ConnectionType.QueuedConnection,
            QtCore.Q_ARG(str, message),
        )

    @QtCore.Slot(str)
    def _on_voice_error_ui(self, message: str) -> None:
        if not self.state.listening_requested:
            return
        self.footer.setText(message)
        self.listen_btn.setText("Listening: ERROR")

    @QtCore.Slot(str)
    def _on_voice_text_ui(self, text: str) -> None:
        try:
            panel_open = self._memory_panel_visible()
            memory_intent = resolve_memory_intent(text, memory_panel_open=panel_open)
            is_memory_cmd = memory_intent is not None
            is_summon = parse_show_intent(text) and not is_memory_cmd
            if not is_memory_cmd and not is_summon and time.monotonic() < self._voice_grace_until:
                return
            if not is_memory_cmd and not is_summon and not self._is_meaningful_voice_text(text):
                return
            if not is_memory_cmd and not is_summon and self._is_tts_echo(text):
                return
            if not is_memory_cmd and not is_summon and self._is_duplicate_voice_text(text):
                return
            if not self.isVisible() and self.state.listening_requested:
                self.state.jarvis_awake = True
            if is_memory_cmd or is_summon:
                self._voice_command_queue.clear()
                self._voice_command_busy = False
                log_voice(f"{'memory' if is_memory_cmd else 'summon'} {text!r}")
                self._apply_text(text, source="voice")
                return
            self._queue_voice_command(text)
        except Exception as exc:
            log_diagnostic("ui", f"voice text handling failed: {text!r}", exc=exc)

    def _queue_voice_command(self, text: str) -> None:
        text = (text or "").strip()
        if not text:
            return
        log_voice(f"heard {text!r}")
        self._voice_command_queue.append(text)
        dropped = 0
        if len(self._voice_command_queue) > self._VOICE_QUEUE_MAX:
            dropped = len(self._voice_command_queue) - self._VOICE_QUEUE_MAX
            self._voice_command_queue = self._voice_command_queue[-self._VOICE_QUEUE_MAX :]
        if dropped:
            self.footer.setText(f"Dropped {dropped} older voice command(s) — queue full.")
        if self._voice_command_busy:
            return
        self._voice_command_busy = True
        self._visual_timer.stop()
        QtCore.QTimer.singleShot(0, self._process_queued_voice_command)

    @QtCore.Slot()
    def _process_queued_voice_command(self) -> None:
        if not self._voice_command_queue:
            self._voice_command_busy = False
            self._sync_fx_timer()
            return
        self._visual_timer.stop()
        text = self._voice_command_queue.pop(0)
        try:
            self._apply_text(text, source="voice")
        except Exception as exc:
            log_diagnostic("ui", f"queued voice command failed: {text!r}", exc=exc)
        if self._voice_command_queue:
            QtCore.QTimer.singleShot(50, self._process_queued_voice_command)
        else:
            self._voice_command_busy = False
            self._sync_fx_timer()

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
        self._jarvis_speaking = speaking
        self.voice.set_results_muted(speaking)
        if speaking:
            self._visual_timer.stop()
        else:
            self._voice_grace_until = time.monotonic() + self._VOICE_GRACE_AFTER_SPEECH_S
            self._sync_fx_timer()

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
            # Block near-verbatim replays of Jarvis speech only (same words, same order).
            if len(words) <= 5 and len(spoken_words) <= 6 and words == spoken_words:
                return True
        return False

    def _on_item_changed(self, item: QtWidgets.QListWidgetItem) -> None:
        if self._refreshing_lists:
            return
        if item.listWidget() is self.open_list:
            self._on_open_item_changed(item)

    def _on_open_item_changed(self, item: QtWidgets.QListWidgetItem) -> None:
        task_id = int(item.data(_ROLE_TASK_ID))
        if item.checkState() == QtCore.Qt.CheckState.Checked:
            self.db.set_status(task_id, "done")
            self.sfx.play("complete")
            self.refresh()
            return

        new_title = normalize_quest_title(item.text())
        task = self.db.get_task(task_id)
        if task is None:
            self.refresh()
            return
        if not new_title or new_title == task.title:
            return
        if self.db.update_task_title(task_id, new_title):
            self.sfx.play("add")
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
        if (
            self.state.listening_requested
            and not self.voice.is_running
            and not self.voice.is_starting
            and not self._voice_start_pending
        ):
            self._begin_voice_listener()
        self._sync_fx_timer()
        self._sync_voice_gate()
        self._sync_privacy_button()

    def showEvent(self, event: QtGui.QShowEvent) -> None:  # noqa: N802
        self.state.jarvis_awake = True
        super().showEvent(event)
        if not self._events_started:
            self._events_started = True
            self._event_service.start()
        self._hide_from_taskbar(self)
        if (
            self.state.listening_requested
            and not self.voice.is_running
            and not self.voice.is_starting
            and not self._voice_start_pending
        ):
            self._begin_voice_listener()
        self._sync_fx_timer()
        self._sync_voice_gate()
        self._sync_privacy_button()
        QtCore.QTimer.singleShot(0, self._position_memory_tab)

    def hideEvent(self, event: QtGui.QHideEvent) -> None:  # noqa: N802
        self._pause_fx_render()
        super().hideEvent(event)
        self._sync_voice_gate()
        self._sync_privacy_button()

    def _set_footer_default(self) -> None:
        self.footer.setText(self._default_footer_text())

    def _default_footer_text(self) -> str:
        if not self.state.listening_requested:
            return "Privacy mode: microphone is off. Click the red button to listen again."
        if not self.isVisible():
            return 'Hidden — say "Jarvis …" to start, then give your command.'
        if self.state.jarvis_awake:
            if self.brain.is_enabled:
                return "Jarvis is awake. Just talk naturally — no exact phrases needed."
            return "Jarvis is awake. Talk naturally; Ollama improves understanding when running."
        if self.brain.is_enabled:
            return 'Say "Jarvis wake up", then talk naturally.'
        return 'Say "Jarvis wake up", or install Ollama for natural speech.'

