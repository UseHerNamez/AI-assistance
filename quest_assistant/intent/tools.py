from __future__ import annotations

from typing import Any, Optional

from quest_assistant.core.types import RiskLevel, ToolCall
from quest_assistant.local_llm import LLMAction
from quest_assistant.parser import (
    extract_delete_quest_number,
    has_add_intent,
    has_complete_intent,
    has_delete_intent,
    has_edit_intent,
    looks_like_delete_intent,
)
from quest_assistant.compose.detect import is_echo_reply, looks_like_compose_intent
from quest_assistant.intent import parser_route
from quest_assistant.system.launcher import is_valid_browser_destination


# Stable tool names for logging, LLM prompts, and permissions (phase 2+).
TOOL_LISTEN_OFF = "listen_off"
TOOL_SET_FX = "set_fx"
TOOL_SHOW = "show_window"
TOOL_HIDE = "hide_window"
TOOL_QUIT = "quit_app"
TOOL_OPEN_BROWSER = "open_browser"
TOOL_OPEN_APP = "open_app"
TOOL_OPEN_URL = "open_url"
TOOL_WEB_SEARCH = "web_search"
TOOL_DOWNLOAD_SEARCH = "download_search"
TOOL_CREATE_TASK = "create_task"
TOOL_CREATE_DAILY_TASK = "create_daily_task"
TOOL_START_ADD = "start_add_mode"
TOOL_STOP_ADD = "stop_add_mode"
TOOL_DELETE_TASK = "delete_task"
TOOL_START_DELETE = "start_delete_mode"
TOOL_COMPLETE_TASK = "complete_task"
TOOL_EDIT_TASK = "edit_task"
TOOL_SPEAK = "speak"
TOOL_SET_FOOTER = "set_footer"
TOOL_APPLY_PENDING_DELETE = "apply_pending_delete"
TOOL_SCHEDULE_TIMER = "schedule_timer"
TOOL_SHOW_MEMORY = "show_memory"
TOOL_HIDE_MEMORY = "hide_memory"


def tool(
    name: str,
    /,
    *,
    risk: RiskLevel = RiskLevel.LOW,
    **arguments: Any,
) -> ToolCall:
    return ToolCall(name=name, arguments=dict(arguments), risk=risk)


_OPEN_TOOL_NAMES = frozenset(
    {
        TOOL_OPEN_BROWSER,
        TOOL_OPEN_APP,
        TOOL_OPEN_URL,
        TOOL_WEB_SEARCH,
        TOOL_DOWNLOAD_SEARCH,
    }
)


def llm_actions_to_tool_calls(
    actions: list[LLMAction],
    *,
    utterance: str,
    allow_add: bool,
    jarvis_awake: bool,
    source: str,
) -> list[ToolCall]:
    """Map legacy LLM JSON actions to the tool registry."""
    delete_like = looks_like_delete_intent(utterance) or has_delete_intent(utterance)
    calls: list[ToolCall] = []
    control_used = False

    for action in actions:
        kind = action.kind
        if kind == "noop":
            continue

        if kind in {"show", "hide", "listen_off", "quit", "set_fx", "open_browser", "open_app", "open_url", "web_search", "download_search", "show_memory", "hide_memory"}:
            if control_used and kind in {"show", "hide", "listen_off", "quit", "set_fx"}:
                continue
            if kind in {"show", "hide", "listen_off", "quit", "set_fx"}:
                control_used = True

        if kind == "show":
            calls.append(tool(TOOL_SHOW))
        elif kind == "hide":
            calls.append(tool(TOOL_HIDE))
        elif kind == "listen_off":
            calls.append(tool(TOOL_LISTEN_OFF))
        elif kind == "quit":
            calls.append(tool(TOOL_QUIT, risk=RiskLevel.HIGH))
        elif kind == "set_fx":
            enabled = (action.value or "").strip().lower() in {"on", "true", "enable", "enabled"}
            calls.append(tool(TOOL_SET_FX, enabled=enabled))
        elif kind == "open_browser":
            url = action.value if is_valid_browser_destination(action.value or "") else None
            calls.append(tool(TOOL_OPEN_BROWSER, url=url))
        elif kind == "open_app":
            calls.append(tool(TOOL_OPEN_APP, name=action.value or action.title or ""))
        elif kind == "open_url":
            calls.append(tool(TOOL_OPEN_URL, target=action.value or ""))
        elif kind == "web_search":
            calls.append(tool(TOOL_WEB_SEARCH, query=action.value or ""))
        elif kind == "download_search":
            calls.append(tool(TOOL_DOWNLOAD_SEARCH, query=action.value or ""))
        elif kind == "show_memory":
            calls.append(tool(TOOL_SHOW_MEMORY))
        elif kind == "hide_memory":
            calls.append(tool(TOOL_HIDE_MEMORY))
        elif kind == "add":
            if not allow_add:
                if delete_like:
                    number = extract_delete_quest_number(utterance)
                    if number is not None:
                        calls.append(
                            tool(TOOL_DELETE_TASK, number=number, risk=RiskLevel.MEDIUM)
                        )
                continue
            if action.title:
                calls.append(tool(TOOL_CREATE_TASK, titles=[action.title]))
            else:
                calls.append(tool(TOOL_START_ADD))
        elif kind == "complete":
            if not has_complete_intent(utterance) and not (jarvis_awake and source == "voice"):
                continue
            calls.append(
                tool(
                    TOOL_COMPLETE_TASK,
                    number=_llm_title_as_number(action.title),
                    title=None if _llm_title_as_number(action.title) else action.title,
                )
            )
        elif kind == "delete":
            if not delete_like:
                continue
            number = _llm_title_as_number(action.title)
            calls.append(
                tool(
                    TOOL_DELETE_TASK,
                    number=number,
                    title=None if number else action.title,
                    risk=RiskLevel.MEDIUM,
                )
            )
        elif kind == "edit" and action.value:
            if not has_edit_intent(utterance):
                continue
            number = _llm_title_as_number(action.title)
            calls.append(
                tool(
                    TOOL_EDIT_TASK,
                    number=number,
                    title=None if number else action.title,
                    new_title=action.value,
                    risk=RiskLevel.MEDIUM,
                )
            )
        elif kind == "reply" and action.value:
            if is_echo_reply(utterance, action.value):
                continue
            calls.append(tool(TOOL_SPEAK, text=action.value))

    if looks_like_compose_intent(utterance):
        return []

    parser_open = [
        call
        for call in parser_route.route_parser_controls(utterance)
        if call.name in _OPEN_TOOL_NAMES
    ]
    if parser_open:
        replies = [call for call in calls if call.name == TOOL_SPEAK]
        return parser_open + replies

    return calls


def _llm_title_as_number(title: Optional[str]) -> Optional[int]:
    if not title:
        return None
    cleaned = str(title).strip().lstrip("#").strip()
    if cleaned.isdigit():
        value = int(cleaned)
        return value if value > 0 else None
    return None
