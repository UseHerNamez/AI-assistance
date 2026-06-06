#!/usr/bin/env python3
"""Run a live Jarvis command sequence through the real UI pipeline."""

from __future__ import annotations

import sys
import time
from dataclasses import dataclass
from pathlib import Path

from PySide6 import QtCore, QtWidgets

from quest_assistant.core.types import RouteKind
from quest_assistant.db import QuestDB
from quest_assistant.intent import tools as T
from quest_assistant.single_instance import acquire_single_instance_lock
from quest_assistant.system.launcher import find_app_path, resolve_site_url
from quest_assistant.ui_widget import QuestWidget

LOG_PATH = Path.home() / ".quest_assistant" / "live_test.log"

QUESTS: list[tuple[str, str]] = [
    ("open youtube", "Open YouTube in browser"),
    ("open reddit", "Open Reddit in browser"),
    ("download vlc", "Browser search to download VLC"),
    ("open outlook", "Launch Microsoft Outlook"),
    ("open league of legends", "Launch League of Legends"),
]

STEP_DELAY_MS = 6000
WAKE_DELAY_MS = 2500


@dataclass
class StepLog:
    command: str
    description: str
    route_kind: str
    tools: list[str]
    preflight: str
    executed: bool


def _log(line: str) -> None:
    stamp = time.strftime("%Y-%m-%d %H:%M:%S")
    msg = f"[{stamp}] {line}"
    print(msg, flush=True)
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with LOG_PATH.open("a", encoding="utf-8") as handle:
            handle.write(msg + "\n")
    except OSError:
        pass


def _preflight(command: str, tools: list[str]) -> str:
    lower = command.lower()
    if T.TOOL_OPEN_URL in tools or "youtube" in lower or "reddit" in lower:
        target = command.split("open", 1)[-1].strip() if "open" in lower else command
        url = resolve_site_url(target.split()[-1] if target else "")
        return f"url={url or 'unknown'}"
    if T.TOOL_DOWNLOAD_SEARCH in tools:
        return "browser search with download intent"
    if T.TOOL_OPEN_APP in tools:
        name = command.split("open", 1)[-1].strip()
        path = find_app_path(name)
        return f"app_path={'FOUND: ' + path if path else 'NOT FOUND'}"
    if T.TOOL_WEB_SEARCH in tools:
        return "web search"
    if T.TOOL_OPEN_BROWSER in tools:
        return "default browser"
    return "n/a"


class LiveQuestRunner(QtCore.QObject):
    def __init__(self, widget: QuestWidget) -> None:
        super().__init__()
        self._widget = widget
        self._index = 0
        self._results: list[StepLog] = []

    def start(self) -> None:
        _log("=== Live quest run started ===")
        _log("Waking Jarvis and showing Assistance window…")
        self._widget.show_and_raise()
        self._widget.state.jarvis_awake = True
        QtCore.QTimer.singleShot(WAKE_DELAY_MS, self._run_next)

    def _run_next(self) -> None:
        if self._index >= len(QUESTS):
            self._finish()
            return

        command, description = QUESTS[self._index]
        self._index += 1
        _log(f"--- Step {self._index}/{len(QUESTS)}: {description} ---")
        _log(f"Command: {command!r}")

        ctx = self._widget._session_context("voice")
        decision = self._widget._router.route(command, ctx)
        tools = [call.name for call in (decision.tool_calls or [])]
        preflight = _preflight(command, tools)
        _log(f"Route: {decision.route_path} kind={decision.kind.value} tools={tools}")
        _log(f"Preflight: {preflight}")

        executed = False
        if decision.kind == RouteKind.EXECUTE and decision.tool_calls:
            result = self._widget._action_host.execute(
                decision.tool_calls,
                route_path=f"live_test:{decision.route_path}",
            )
            executed = result.ok
            _log(f"Execute ok={result.ok} spoke={result.spoke}")
        else:
            _log(f"NOT EXECUTED (routed to {decision.kind.value})")

        self._results.append(
            StepLog(
                command=command,
                description=description,
                route_kind=decision.kind.value,
                tools=tools,
                preflight=preflight,
                executed=executed,
            )
        )

        QtCore.QTimer.singleShot(STEP_DELAY_MS, self._run_next)

    def _finish(self) -> None:
        _log("=== Live quest run complete ===")
        for index, step in enumerate(self._results, start=1):
            _log(
                f"Summary {index}: {step.command!r} -> tools={step.tools} "
                f"executed={step.executed} preflight={step.preflight}"
            )
        _log("Assistance stays open — tell me which steps worked on your screen.")
        QtWidgets.QApplication.instance().quit()


def main() -> int:
    lock = acquire_single_instance_lock()
    if lock is None:
        print("Another Assistance instance is already running. Stop it first.", file=sys.stderr)
        return 1

    app = QtWidgets.QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    app.aboutToQuit.connect(lock.unlock)

    db = QuestDB()
    widget = QuestWidget(db)
    runner = LiveQuestRunner(widget)
    QtCore.QTimer.singleShot(500, runner.start)

    code = app.exec()
    db.close()
    try:
        widget.memory.close()
    except Exception:
        pass
    return code


if __name__ == "__main__":
    raise SystemExit(main())
