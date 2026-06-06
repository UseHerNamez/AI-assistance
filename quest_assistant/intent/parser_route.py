from __future__ import annotations

import re
from typing import Optional

from quest_assistant.core.session import SessionContext
from quest_assistant.core.types import RiskLevel, ToolCall
from quest_assistant.intent import tools as T
from quest_assistant.memory.detect import parse_hide_memory_intent, parse_show_memory_intent, resolve_memory_intent
from quest_assistant.parser import (
    extract_add_titles,
    extract_daily_add_titles,
    extract_delete_quest_number,
    extract_quest_titles,
    has_add_intent,
    has_daily_add_intent,
    has_numbered_quest_markers,
    infer_casual_intent,
    is_delete_title_placeholder,
    looks_like_delete_intent,
    looks_like_daily_typed_quest,
    looks_like_listen_off,
    looks_like_typed_quest,
    normalize_quest_title,
    normalize_voice_command,
    parse_action,
    parse_fx_enabled,
    parse_hide_intent,
    parse_open_browser_intent,
    parse_open_target,
    parse_download_search_query,
    parse_quit_intent,
    parse_quest_number,
    parse_show_intent,
    parse_web_search_query,
    resolve_fx_enabled,
    split_into_items,
)


def resolve_fx_for_voice(text: str, *, fx_visually_on: bool) -> Optional[bool]:
    fx = resolve_fx_enabled(text)
    if fx is not None:
        return fx
    if not fx_visually_on or looks_like_listen_off(text):
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


def route_pending_delete(text: str) -> list[ToolCall]:
    if looks_like_listen_off(text) or parse_quit_intent(text):
        return []
    if has_add_intent(text):
        return []

    action = parse_action(text, allow_implicit_add=False)
    if action.kind == "delete":
        if action.quest_number is not None:
            return [T.tool(T.TOOL_DELETE_TASK, number=action.quest_number, risk=RiskLevel.MEDIUM)]
        if action.title and not is_delete_title_placeholder(action.title):
            return [T.tool(T.TOOL_DELETE_TASK, title=action.title, risk=RiskLevel.MEDIUM)]

    raw = normalize_voice_command(text)
    if not raw:
        return []
    words = raw.split()
    if len(words) == 1:
        number = parse_quest_number(words[0])
        if number is not None:
            return [T.tool(T.TOOL_DELETE_TASK, number=number, risk=RiskLevel.MEDIUM)]

    title = normalize_quest_title(raw)
    if title:
        return [T.tool(T.TOOL_DELETE_TASK, title=title, risk=RiskLevel.MEDIUM)]
    return []


def route_parser_controls(text: str) -> list[ToolCall]:
    if looks_like_listen_off(text):
        return [T.tool(T.TOOL_LISTEN_OFF)]

    if parse_show_intent(text):
        return [T.tool(T.TOOL_SHOW)]
    if parse_hide_intent(text):
        return [T.tool(T.TOOL_HIDE)]
    if parse_quit_intent(text):
        return [T.tool(T.TOOL_QUIT, risk=RiskLevel.HIGH)]

    fx = resolve_fx_enabled(text)
    if fx is not None:
        return [T.tool(T.TOOL_SET_FX, enabled=fx)]

    open_target = parse_open_target(text)
    if open_target is not None:
        kind, target = open_target
        if kind == "url":
            return [T.tool(T.TOOL_OPEN_URL, target=target)]
        return [T.tool(T.TOOL_OPEN_APP, name=target)]

    download_query = parse_download_search_query(text)
    if download_query:
        return [T.tool(T.TOOL_DOWNLOAD_SEARCH, query=download_query)]

    if parse_open_browser_intent(text):
        return [T.tool(T.TOOL_OPEN_BROWSER)]
    query = parse_web_search_query(text)
    if query:
        return [T.tool(T.TOOL_WEB_SEARCH, query=query)]

    action = parse_action(text, allow_implicit_add=False)
    if action.kind == "download_search":
        return [T.tool(T.TOOL_DOWNLOAD_SEARCH, query=action.value or "")]
    if action.kind == "open_app":
        return [T.tool(T.TOOL_OPEN_APP, name=action.value or "")]
    if action.kind == "open_url":
        return [T.tool(T.TOOL_OPEN_URL, target=action.value or "")]
    if action.kind == "web_search":
        return [T.tool(T.TOOL_WEB_SEARCH, query=action.value or "")]
    if action.kind == "open_browser":
        return [T.tool(T.TOOL_OPEN_BROWSER)]
    if action.kind == "show":
        return [T.tool(T.TOOL_SHOW)]
    if action.kind == "hide":
        return [T.tool(T.TOOL_HIDE)]
    if action.kind == "listen_off":
        return [T.tool(T.TOOL_LISTEN_OFF)]
    if action.kind == "quit":
        return [T.tool(T.TOOL_QUIT, risk=RiskLevel.HIGH)]
    return []


def route_memory_controls(text: str) -> list[ToolCall]:
    intent = resolve_memory_intent(text)
    if intent == "hide":
        return [T.tool(T.TOOL_HIDE_MEMORY)]
    if intent == "show":
        return [T.tool(T.TOOL_SHOW_MEMORY)]
    return []


def route_casual_intent(text: str) -> list[ToolCall]:
    action = infer_casual_intent(text)
    if not action:
        return []
    if action.kind == "set_fx":
        enabled = (action.value or "on").strip().lower() == "on"
        return [T.tool(T.TOOL_SET_FX, enabled=enabled)]
    if action.kind == "show":
        return [T.tool(T.TOOL_SHOW)]
    if action.kind == "hide":
        return [T.tool(T.TOOL_HIDE)]
    if action.kind == "listen_off" and looks_like_listen_off(text):
        return [T.tool(T.TOOL_LISTEN_OFF)]
    return []


def route_quest_command(text: str) -> list[ToolCall]:
    action = parse_action(text, allow_implicit_add=False)
    if action.kind == "delete":
        if action.quest_number is not None:
            return [T.tool(T.TOOL_DELETE_TASK, number=action.quest_number, risk=RiskLevel.MEDIUM)]
        if action.title and not is_delete_title_placeholder(action.title):
            return [T.tool(T.TOOL_DELETE_TASK, title=action.title, risk=RiskLevel.MEDIUM)]
        number = extract_delete_quest_number(text)
        if number is not None:
            return [T.tool(T.TOOL_DELETE_TASK, number=number, risk=RiskLevel.MEDIUM)]
        if looks_like_delete_intent(text):
            return [T.tool(T.TOOL_START_DELETE)]
        return []

    if action.kind == "complete" and (action.title is not None or action.quest_number is not None):
        return [
            T.tool(
                T.TOOL_COMPLETE_TASK,
                number=action.quest_number,
                title=action.title,
            )
        ]
    if action.kind == "edit" and action.value and (action.title is not None or action.quest_number is not None):
        return [
            T.tool(
                T.TOOL_EDIT_TASK,
                number=action.quest_number,
                title=action.title,
                new_title=action.value,
                risk=RiskLevel.MEDIUM,
            )
        ]
    return []


def _allow_implicit_add(text: str, ctx: SessionContext) -> bool:
    from quest_assistant.parser import looks_like_chat

    if looks_like_chat(text):
        return False
    if ctx.pending_delete:
        return False
    if ctx.pending_add:
        return True
    if has_add_intent(text):
        return True
    if ctx.source == "typed":
        return looks_like_daily_typed_quest(text) or looks_like_typed_quest(text)
    return False


def route_parser_actions(text: str, ctx: SessionContext) -> list[ToolCall]:
    calls: list[ToolCall] = []
    items = (
        [text]
        if extract_add_titles(text) or extract_daily_add_titles(text) or ctx.pending_add
        else split_into_items(text)
    )
    implicit_add = _allow_implicit_add(text, ctx)

    for item in items:
        action = parse_action(item, allow_implicit_add=implicit_add)
        if action.kind in {"show", "hide", "listen_off", "add_done", "quit"}:
            calls.extend(_action_kind_to_tools(action))
            continue

        if action.kind == "add_daily":
            titles = extract_daily_add_titles(item)
            if not titles and action.title:
                titles = [action.title]
            titles = [t for t in (normalize_quest_title(t) for t in titles) if t]
            if titles:
                calls.append(
                    T.tool(
                        T.TOOL_CREATE_DAILY_TASK,
                        titles=titles,
                        raw_input=action.raw,
                        source=ctx.source,
                    )
                )
            else:
                calls.append(T.tool(T.TOOL_START_ADD, daily=True))
            continue

        if action.kind == "add":
            titles = extract_quest_titles(item) if ctx.pending_add else extract_add_titles(item)
            if not titles and action.title:
                titles = [action.title]
            titles = [t for t in (normalize_quest_title(t) for t in titles) if t]
            if titles:
                continue_collection = (
                    ctx.source == "voice"
                    and ctx.pending_add
                    and len(titles) == 1
                    and has_numbered_quest_markers(item)
                )
                calls.append(
                    T.tool(
                        T.TOOL_CREATE_TASK,
                        titles=titles,
                        due_iso=action.due_iso,
                        raw_input=action.raw,
                        source=ctx.source,
                        continue_collection=continue_collection,
                    )
                )
            else:
                calls.append(T.tool(T.TOOL_START_ADD))
            continue

        if action.kind == "complete" and (action.title or action.quest_number is not None):
            calls.append(
                T.tool(T.TOOL_COMPLETE_TASK, number=action.quest_number, title=action.title)
            )
        elif action.kind == "delete" and (action.title or action.quest_number is not None):
            calls.append(
                T.tool(
                    T.TOOL_DELETE_TASK,
                    number=action.quest_number,
                    title=action.title,
                    risk=RiskLevel.MEDIUM,
                )
            )
        elif action.kind == "edit" and action.value and (action.title or action.quest_number is not None):
            calls.append(
                T.tool(
                    T.TOOL_EDIT_TASK,
                    number=action.quest_number,
                    title=action.title,
                    new_title=action.value,
                    risk=RiskLevel.MEDIUM,
                )
            )

    return calls


def _action_kind_to_tools(action) -> list[ToolCall]:  # noqa: ANN001
    if action.kind == "show":
        return [T.tool(T.TOOL_SHOW)]
    if action.kind == "hide":
        return [T.tool(T.TOOL_HIDE)]
    if action.kind == "listen_off":
        return [T.tool(T.TOOL_LISTEN_OFF)]
    if action.kind == "add_done":
        return [T.tool(T.TOOL_STOP_ADD), T.tool(T.TOOL_SPEAK, text="Understood.")]
    if action.kind == "quit":
        return [T.tool(T.TOOL_QUIT, risk=RiskLevel.HIGH)]
    return []


def route_fx_voice(text: str, *, fx_visually_on: bool) -> list[ToolCall]:
    fx = resolve_fx_for_voice(text, fx_visually_on=fx_visually_on)
    if fx is None:
        return []
    return [T.tool(T.TOOL_SET_FX, enabled=fx)]
