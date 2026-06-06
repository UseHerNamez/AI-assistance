from __future__ import annotations

from typing import TYPE_CHECKING, Any, Optional

from quest_assistant.core.types import RiskLevel, ToolCall, ToolResult
from quest_assistant.permissions.policy import PermissionSession, effective_risk, partition_calls
from quest_assistant.intent import parser_route, tools as T
from quest_assistant.intent.tools import llm_actions_to_tool_calls
from quest_assistant.local_llm import LLMAction
from quest_assistant.monitor.logger import log_action, log_error
from quest_assistant.parser import (
    extract_add_titles,
    extract_delete_quest_number,
    has_add_intent,
    has_complete_intent,
    has_delete_intent,
    has_edit_intent,
    looks_like_chat,
    looks_like_delete_intent,
    looks_like_listen_off,
    parse_hide_intent,
    parse_quit_intent,
)

if TYPE_CHECKING:
    from quest_assistant.ui_widget import QuestWidget


class QuestActionHost:
    """Executes tool calls against the Qt widget (UI thread only)."""

    def __init__(self, widget: QuestWidget) -> None:
        self._w = widget

    def execute(self, calls: list[ToolCall], *, route_path: str) -> ToolResult:
        session: PermissionSession = self._w._permission_session
        run_now, prompt_first = partition_calls(calls, session)

        aggregate = ToolResult(ok=True)
        for call in run_now:
            aggregate = self._merge_result(aggregate, self._run_call(call, route_path=route_path))

        if not prompt_first:
            return aggregate

        if not self._w.request_tool_confirmation(prompt_first):
            self._w._jarvis_say("Cancelled, sir.")
            aggregate.ok = False
            return aggregate

        if any(effective_risk(c) == RiskLevel.MEDIUM for c in prompt_first):
            session.medium_confirmed = True

        for call in prompt_first:
            aggregate = self._merge_result(
                aggregate,
                self._run_call(call, route_path=route_path, permission_confirmed=True),
            )
        return aggregate

    def _merge_result(self, aggregate: ToolResult, result: ToolResult) -> ToolResult:
        aggregate.spoke = aggregate.spoke or result.spoke
        aggregate.refresh = aggregate.refresh or result.refresh
        aggregate.stop = aggregate.stop or result.stop
        if not result.ok:
            aggregate.ok = False
        return aggregate

    def _run_call(
        self,
        call: ToolCall,
        *,
        route_path: str,
        permission_confirmed: bool = False,
    ) -> ToolResult:
        try:
            result = self._execute_one(call, permission_confirmed=permission_confirmed)
            log_action(f"{route_path} {call.name} {call.arguments!r} ok={result.ok}")
            return result
        except Exception as exc:
            log_error(f"tool {call.name} failed", exc=exc)
            return ToolResult(ok=False)

    def execute_llm_actions(
        self,
        actions: list[LLMAction],
        *,
        utterance: str,
        source: str,
    ) -> bool:
        """Convert LLM actions to tools and run them. Returns True if anything handled."""
        delete_like = looks_like_delete_intent(utterance) or has_delete_intent(utterance)
        if delete_like:
            fx_calls = parser_route.route_fx_voice(utterance, fx_visually_on=self._w._fx_is_visually_on())
            if fx_calls:
                self.execute(fx_calls, route_path="llm_fx_guard")
            quest_calls = parser_route.route_quest_command(utterance)
            if quest_calls:
                self.execute(quest_calls, route_path="llm_quest_guard")
                return True

        allow_add = (
            not delete_like
            and not has_complete_intent(utterance)
            and not has_edit_intent(utterance)
            and not looks_like_chat(utterance)
            and (
                self._w._pending_voice_add
                or has_add_intent(utterance)
                or bool(extract_add_titles(utterance))
            )
        )
        calls = llm_actions_to_tool_calls(
            actions,
            utterance=utterance,
            allow_add=allow_add,
            jarvis_awake=self._w.state.jarvis_awake,
            source=source,
        )
        if not calls:
            return False
        result = self.execute(calls, route_path="llm_tools")
        return result.spoke or result.refresh

    def _execute_one(self, call: ToolCall, *, permission_confirmed: bool = False) -> ToolResult:
        name = call.name
        args = call.arguments
        w = self._w

        if name == T.TOOL_SPEAK:
            text = str(args.get("text") or "").strip()
            if text:
                w._jarvis_say(text)
                return ToolResult(ok=True, spoke=True)
            return ToolResult(ok=True)

        if name == T.TOOL_SET_FOOTER:
            w.footer.setText(str(args.get("text") or ""))
            return ToolResult(ok=True)

        if name == T.TOOL_LISTEN_OFF:
            ok = w._apply_nonquest_llm_action(LLMAction(kind="listen_off"))
            return ToolResult(ok=ok, spoke=True, refresh=True)

        if name == T.TOOL_SET_FX:
            ok = w._apply_nonquest_llm_action(
                LLMAction(kind="set_fx", value="on" if args.get("enabled") else "off")
            )
            return ToolResult(ok=ok, spoke=True)

        if name == T.TOOL_SHOW:
            ok = w._apply_nonquest_llm_action(LLMAction(kind="show"))
            return ToolResult(ok=ok, spoke=True, refresh=True)

        if name == T.TOOL_HIDE:
            ok = w._apply_nonquest_llm_action(LLMAction(kind="hide"))
            return ToolResult(ok=ok, spoke=True, refresh=True)

        if name == T.TOOL_QUIT:
            if permission_confirmed:
                w._quit_assistance_confirmed()
                return ToolResult(ok=True, spoke=True, stop=True)
            ok = w._apply_nonquest_llm_action(LLMAction(kind="quit"))
            return ToolResult(ok=ok, spoke=True, stop=ok)

        if name == T.TOOL_OPEN_BROWSER:
            ok = w._apply_nonquest_llm_action(
                LLMAction(kind="open_browser", value=args.get("url"))
            )
            return ToolResult(ok=ok, spoke=True)

        if name == T.TOOL_OPEN_APP:
            ok = w._apply_nonquest_llm_action(
                LLMAction(kind="open_app", value=str(args.get("name") or ""))
            )
            return ToolResult(ok=ok, spoke=True)

        if name == T.TOOL_OPEN_URL:
            ok = w._apply_nonquest_llm_action(
                LLMAction(kind="open_url", value=str(args.get("target") or ""))
            )
            return ToolResult(ok=ok, spoke=True)

        if name == T.TOOL_WEB_SEARCH:
            ok = w._apply_nonquest_llm_action(LLMAction(kind="web_search", value=args.get("query")))
            return ToolResult(ok=ok, spoke=True)

        if name == T.TOOL_DOWNLOAD_SEARCH:
            ok = w._apply_nonquest_llm_action(
                LLMAction(kind="download_search", value=args.get("query"))
            )
            return ToolResult(ok=ok, spoke=True)

        if name == T.TOOL_START_ADD:
            w._set_pending_add(True)
            w._set_pending_delete(False)
            if args.get("daily"):
                w.footer.setText('Say the daily quest, e.g. "daily brush teeth".')
                w._jarvis_say("Of course. Tell me the daily quest to repeat every day.")
            else:
                w.footer.setText('Say the quest naturally, or say "next quest..." for more.')
                w._jarvis_say("Of course. Tell me the quest, or say next quest for more.")
            return ToolResult(ok=True, spoke=True)

        if name == T.TOOL_STOP_ADD:
            w._set_pending_add(False)
            w._set_footer_default()
            return ToolResult(ok=True, spoke=True, refresh=True)

        if name == T.TOOL_START_DELETE:
            w._set_pending_add(False)
            w._set_pending_delete(True)
            w.footer.setText("Say the quest name or number to delete.")
            w._jarvis_say("Which quest should I delete, sir? Say the name or task number.")
            return ToolResult(ok=True, spoke=True)

        if name == T.TOOL_CREATE_DAILY_TASK:
            titles = list(args.get("titles") or [])
            raw_input = args.get("raw_input")
            source = args.get("source") or getattr(w, "_last_apply_source", "voice")
            added = 0
            for title in titles:
                if w.db.add_daily_task(title, source=source, raw_input=raw_input) is not None:
                    added += 1
            if not added:
                return ToolResult(ok=False)
            w.sfx.play("add")
            for title in titles:
                try:
                    w.memory.record_quest_event("add_daily", title=title)
                except Exception:
                    pass
            if added == 1:
                w._jarvis_say(f"Done, sir. I added the daily quest: {titles[0]}.")
            else:
                w._jarvis_say(f"Done, sir. I added {added} daily quests.")
            w._set_pending_add(False)
            w._set_footer_default()
            return ToolResult(ok=True, spoke=True, refresh=True)

        if name == T.TOOL_CREATE_TASK:
            titles = list(args.get("titles") or [])
            due_iso = args.get("due_iso")
            raw_input = args.get("raw_input")
            source = args.get("source") or getattr(w, "_last_apply_source", "voice")
            added = 0
            for title in titles:
                if w.db.add_task(title, due_iso=due_iso, source=source, raw_input=raw_input) is not None:
                    added += 1
            if not added:
                return ToolResult(ok=False)
            w.sfx.play("add")
            for title in titles:
                try:
                    w.memory.record_quest_event("add", title=title)
                except Exception:
                    pass
            if added == 1:
                w._jarvis_say(f"Done, sir. I added the quest: {titles[0]}.")
            else:
                w._jarvis_say(f"Done, sir. I added {added} quests.")
            if args.get("continue_collection"):
                w._set_pending_add(True)
                w.footer.setText('Keep saying the next quest, or say "Jarvis stop adding".')
            else:
                w._set_pending_add(False)
                w._set_footer_default()
            return ToolResult(ok=True, spoke=True, refresh=True)

        if name == T.TOOL_DELETE_TASK:
            w._set_pending_delete(False)
            number = args.get("number")
            title = args.get("title")
            ok = w._delete_open_quest(
                title=title if number is None else None,
                number=number,
            )
            if ok:
                try:
                    w.memory.record_quest_event("delete", title=title or "", number=number)
                except Exception:
                    pass
            return ToolResult(ok=ok, spoke=True, refresh=ok)

        if name == T.TOOL_COMPLETE_TASK:
            ok = w._complete_open_quest(title=args.get("title"), number=args.get("number"))
            if ok:
                try:
                    w.memory.record_quest_event(
                        "complete",
                        title=args.get("title") or "",
                        number=args.get("number"),
                    )
                except Exception:
                    pass
            return ToolResult(ok=ok, spoke=True, refresh=ok)

        if name == T.TOOL_EDIT_TASK:
            w._edit_open_quest(
                new_title=str(args.get("new_title") or ""),
                title=args.get("title"),
                number=args.get("number"),
            )
            return ToolResult(ok=True, spoke=True, refresh=True)

        if name == T.TOOL_SCHEDULE_TIMER:
            label = str(args.get("label") or "Timer").strip()
            delay_s = float(args.get("delay_s") or 60.0)
            service = getattr(w, "_event_service", None)
            if service is None:
                return ToolResult(ok=False)
            service.timers.schedule(label, delay_s)
            mins = max(1, int(round(delay_s / 60))) if delay_s >= 60 else 0
            if mins:
                spoken = f"Timer set for {mins} minute{'s' if mins != 1 else ''}, sir."
            else:
                spoken = f"Timer set for {int(delay_s)} seconds, sir."
            w._jarvis_say(spoken)
            return ToolResult(ok=True, spoke=True)

        if name == T.TOOL_SHOW_MEMORY:
            w.show_memory_panel(announce=(getattr(w, "_last_apply_source", "voice") == "voice"))
            return ToolResult(ok=True, refresh=True)

        if name == T.TOOL_HIDE_MEMORY:
            w.hide_memory_panel(announce=(getattr(w, "_last_apply_source", "voice") == "voice"))
            return ToolResult(ok=True, spoke=True, refresh=True)

        log_action(f"unknown tool {name}")
        return ToolResult(ok=False)
