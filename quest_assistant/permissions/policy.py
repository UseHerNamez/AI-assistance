from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from quest_assistant.core.types import RiskLevel, ToolCall
from quest_assistant.intent import tools as T


# Default risk when a tool() call omits risk= (declaration of record for permissions).
TOOL_RISK: dict[str, RiskLevel] = {
    T.TOOL_OPEN_BROWSER: RiskLevel.LOW,
    T.TOOL_OPEN_APP: RiskLevel.LOW,
    T.TOOL_OPEN_URL: RiskLevel.LOW,
    T.TOOL_WEB_SEARCH: RiskLevel.LOW,
    T.TOOL_DOWNLOAD_SEARCH: RiskLevel.LOW,
    T.TOOL_CREATE_TASK: RiskLevel.LOW,
    T.TOOL_START_ADD: RiskLevel.LOW,
    T.TOOL_STOP_ADD: RiskLevel.LOW,
    T.TOOL_COMPLETE_TASK: RiskLevel.LOW,
    T.TOOL_DELETE_TASK: RiskLevel.MEDIUM,
    T.TOOL_START_DELETE: RiskLevel.LOW,
    T.TOOL_EDIT_TASK: RiskLevel.MEDIUM,
    T.TOOL_APPLY_PENDING_DELETE: RiskLevel.MEDIUM,
    T.TOOL_QUIT: RiskLevel.HIGH,
    T.TOOL_LISTEN_OFF: RiskLevel.LOW,
    T.TOOL_SET_FX: RiskLevel.LOW,
    T.TOOL_SHOW: RiskLevel.LOW,
    T.TOOL_HIDE: RiskLevel.LOW,
    T.TOOL_SPEAK: RiskLevel.LOW,
    T.TOOL_SET_FOOTER: RiskLevel.LOW,
    T.TOOL_SCHEDULE_TIMER: RiskLevel.LOW,
    T.TOOL_SHOW_MEMORY: RiskLevel.LOW,
    T.TOOL_HIDE_MEMORY: RiskLevel.LOW,
}


@dataclass
class PermissionSession:
    """Per-app-session permission state (not persisted)."""

    medium_confirmed: bool = False


def effective_risk(call: ToolCall) -> RiskLevel:
    if call.risk != RiskLevel.LOW:
        return call.risk
    return TOOL_RISK.get(call.name, RiskLevel.LOW)


def needs_confirmation(call: ToolCall, session: PermissionSession) -> bool:
    risk = effective_risk(call)
    if risk == RiskLevel.LOW:
        return False
    if risk == RiskLevel.MEDIUM:
        return not session.medium_confirmed
    return True


def partition_calls(
    calls: list[ToolCall], session: PermissionSession
) -> tuple[list[ToolCall], list[ToolCall]]:
    """Split into (run_now, prompt_first). Low-risk and already-cleared medium run_now."""
    run_now: list[ToolCall] = []
    prompt_first: list[ToolCall] = []
    for call in calls:
        if needs_confirmation(call, session):
            prompt_first.append(call)
        else:
            run_now.append(call)
    return run_now, prompt_first


def describe_call(call: ToolCall) -> str:
    name = call.name
    args = call.arguments
    if name == T.TOOL_DELETE_TASK:
        number = args.get("number")
        title = args.get("title")
        if number is not None:
            return f"Delete quest #{number}"
        if title:
            return f'Delete quest "{title}"'
        return "Delete a quest"
    if name == T.TOOL_EDIT_TASK:
        number = args.get("number")
        new_title = args.get("new_title") or ""
        target = f"#{number}" if number is not None else f'"{args.get("title") or ""}"'
        return f"Edit quest {target} → {new_title!r}"
    if name == T.TOOL_QUIT:
        return "Quit Assistance"
    if name == T.TOOL_COMPLETE_TASK:
        number = args.get("number")
        if number is not None:
            return f"Complete quest #{number}"
        return f'Complete quest "{args.get("title") or ""}"'
    if name == T.TOOL_CREATE_TASK:
        titles = args.get("titles") or []
        if len(titles) == 1:
            return f'Add quest "{titles[0]}"'
        if titles:
            return f"Add {len(titles)} quests"
        return "Add quest"
    if name == T.TOOL_WEB_SEARCH:
        return f'Search the web for "{args.get("query") or ""}"'
    if name == T.TOOL_DOWNLOAD_SEARCH:
        return f'Find a download for "{args.get("query") or ""}"'
    if name == T.TOOL_OPEN_BROWSER:
        url = args.get("url")
        if url:
            return f"Open browser to {url}"
        return "Open web browser"
    if name == T.TOOL_OPEN_APP:
        return f'Open "{args.get("name") or ""}"'
    if name == T.TOOL_OPEN_URL:
        return f'Open "{args.get("target") or ""}" in browser'
    if name == T.TOOL_SET_FX:
        return "Turn visual effects " + ("on" if args.get("enabled") else "off")
    if name == T.TOOL_HIDE:
        return "Hide Assistance window"
    if name == T.TOOL_SHOW:
        return "Show Assistance window"
    if name == T.TOOL_LISTEN_OFF:
        return "Turn off microphone listening"
    if name == T.TOOL_SHOW_MEMORY:
        return "Show stored memory"
    if name == T.TOOL_HIDE_MEMORY:
        return "Hide memory panel"
    return name.replace("_", " ").title()


def dialog_hint(calls: Iterable[ToolCall]) -> str:
    risks = {effective_risk(c) for c in calls}
    if RiskLevel.HIGH in risks:
        return "High-risk actions always require your confirmation."
    if RiskLevel.MEDIUM in risks:
        return "Medium-risk actions will not ask again this session after you approve."
    return ""
