from __future__ import annotations

from quest_assistant.core.session import SessionContext
from quest_assistant.core.types import RouteDecision, RouteKind
from quest_assistant.intent import parser_route
from quest_assistant.monitor.logger import TimedRoute
from quest_assistant.parser import (
    has_add_intent,
    has_complete_intent,
    has_delete_intent,
    has_edit_intent,
    looks_like_delete_intent,
    looks_like_listen_off,
    parse_fx_enabled,
    parse_hide_intent,
    parse_open_browser_intent,
    parse_open_target,
    parse_download_search_query,
    parse_quit_intent,
    parse_show_intent,
    parse_web_search_query,
    resolve_fx_enabled,
)
from quest_assistant.parser import looks_like_typed_quest
from quest_assistant.events.sources.timers import parse_timer_request
from quest_assistant.planner.detect import try_build_research_plan
from quest_assistant.compose.detect import parse_compose_request, resolve_compose_request
from quest_assistant.memory.detect import looks_like_memory_panel_intent, resolve_memory_intent
from quest_assistant.compose.models import ComposeRequest
from quest_assistant.intent import tools as T
from quest_assistant.vision.detect import looks_like_vision_request, vision_user_prompt


class IntentRouter:
    """
    Voice/text → tool calls (parser first). LLM is a separate route decision.
    """

    def route(self, text: str, ctx: SessionContext) -> RouteDecision:
        timer = TimedRoute("route", text)

        if ctx.pending_delete:
            calls = parser_route.route_pending_delete(text)
            if calls:
                timer.finish(kind="execute", tool_count=len(calls))
                return RouteDecision(
                    kind=RouteKind.EXECUTE,
                    route_path="pending_delete",
                    tool_calls=calls,
                    instant=True,
                )

        if looks_like_listen_off(text):
            calls = parser_route.route_parser_controls(text)
            if calls:
                timer.finish(kind="execute", tool_count=len(calls))
                return RouteDecision(
                    kind=RouteKind.EXECUTE,
                    route_path="listen_off",
                    tool_calls=calls,
                    instant=True,
                )

        calls = parser_route.route_memory_controls(text)
        if calls:
            timer.finish(kind="execute", tool_count=len(calls))
            return RouteDecision(
                kind=RouteKind.EXECUTE,
                route_path="memory",
                tool_calls=calls,
                instant=True,
            )

        if looks_like_vision_request(text):
            timer.finish(kind="vision", tool_count=0)
            return RouteDecision(
                kind=RouteKind.VISION,
                route_path="vision",
                vision_prompt=vision_user_prompt(text),
                instant=False,
            )

        timer_parsed = parse_timer_request(text)
        if timer_parsed:
            label, delay_s = timer_parsed
            timer.finish(kind="execute", tool_count=1)
            return RouteDecision(
                kind=RouteKind.EXECUTE,
                route_path="timer",
                tool_calls=[
                    T.tool(
                        T.TOOL_SCHEDULE_TIMER,
                        label=label,
                        delay_s=delay_s,
                    )
                ],
                instant=True,
            )

        compose = resolve_compose_request(text)
        if compose:
            if ctx.may_use_ollama:
                timer.finish(kind="compose", tool_count=0)
                return RouteDecision(
                    kind=RouteKind.COMPOSE,
                    route_path="compose",
                    compose_request=compose,
                    instant=False,
                )
            timer.finish(kind="typed_hint", tool_count=0)
            return RouteDecision(
                kind=RouteKind.TYPED_HINT,
                route_path="compose_needs_llm",
                footer="Drafting needs Ollama running locally. Start Ollama, then try again.",
            )

        plan = try_build_research_plan(text)
        if plan and ctx.may_use_ollama and not ctx.pending_add and not ctx.pending_delete:
            timer.finish(kind="plan", tool_count=0)
            return RouteDecision(
                kind=RouteKind.PLAN,
                route_path="planner",
                research_plan=plan,
                instant=False,
            )

        calls = parser_route.route_fx_voice(text, fx_visually_on=ctx.fx_visually_on)
        if calls:
            timer.finish(kind="execute", tool_count=len(calls))
            return RouteDecision(
                kind=RouteKind.EXECUTE,
                route_path="fx",
                tool_calls=calls,
                instant=True,
            )

        calls = parser_route.route_parser_controls(text)
        if calls:
            timer.finish(kind="execute", tool_count=len(calls))
            return RouteDecision(
                kind=RouteKind.EXECUTE,
                route_path="controls",
                tool_calls=calls,
                instant=True,
            )

        calls = parser_route.route_casual_intent(text)
        if calls:
            timer.finish(kind="execute", tool_count=len(calls))
            return RouteDecision(
                kind=RouteKind.EXECUTE,
                route_path="casual",
                tool_calls=calls,
                instant=True,
            )

        if looks_like_delete_intent(text):
            calls = parser_route.route_quest_command(text)
            if calls:
                timer.finish(kind="execute", tool_count=len(calls))
                return RouteDecision(
                    kind=RouteKind.EXECUTE,
                    route_path="quest_delete",
                    tool_calls=calls,
                    instant=True,
                )

        calls = parser_route.route_quest_command(text)
        if calls:
            timer.finish(kind="execute", tool_count=len(calls))
            return RouteDecision(
                kind=RouteKind.EXECUTE,
                route_path="quest",
                tool_calls=calls,
                instant=True,
            )

        if ctx.source == "voice" and ctx.jarvis_awake and (ctx.pending_add or has_add_intent(text)):
            calls = parser_route.route_parser_actions(text, ctx)
            if calls:
                timer.finish(kind="execute", tool_count=len(calls))
                return RouteDecision(
                    kind=RouteKind.EXECUTE,
                    route_path="parser_add",
                    tool_calls=calls,
                    instant=True,
                )

        if self._should_dispatch_llm_awake(text, ctx):
            timer.finish(kind="llm", tool_count=0)
            return RouteDecision(
                kind=RouteKind.LLM,
                route_path="llm_awake",
                instant=False,
            )

        calls = parser_route.route_parser_actions(text, ctx)
        if calls:
            timer.finish(kind="execute", tool_count=len(calls))
            return RouteDecision(
                kind=RouteKind.EXECUTE,
                route_path="parser",
                tool_calls=calls,
                instant=True,
            )

        if ctx.may_use_ollama and self._should_dispatch_llm(text, ctx):
            timer.finish(kind="llm", tool_count=0)
            return RouteDecision(
                kind=RouteKind.LLM,
                route_path="llm",
                instant=False,
            )

        if ctx.source == "typed" and not looks_like_typed_quest(text):
            timer.finish(kind="typed_hint", tool_count=0)
            return RouteDecision(
                kind=RouteKind.TYPED_HINT,
                route_path="typed_hint",
                footer='Type a quest title, or say "add …" for voice-style commands.',
            )

        if ctx.jarvis_awake and ctx.source == "voice":
            timer.finish(kind="conversation", tool_count=0)
            return RouteDecision(kind=RouteKind.CONVERSATION, route_path="conversation")

        timer.finish(kind="noop", tool_count=0)
        return RouteDecision(kind=RouteKind.NOOP, route_path="noop")

    @staticmethod
    def _should_dispatch_llm_awake(text: str, ctx: SessionContext) -> bool:
        cleaned = " ".join((text or "").split())
        if len(cleaned) < 2:
            return False
        words = [w.strip(".,!?;:") for w in cleaned.lower().split()]
        if not words:
            return False
        if len(words) == 1 and words[0] in ctx.voice_filler_words:
            return False
        if ctx.pending_add or has_add_intent(text):
            return False
        if ctx.pending_delete:
            return False
        if looks_like_listen_off(text) or parse_quit_intent(text) or parse_show_intent(text):
            return False
        if looks_like_memory_panel_intent(text):
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
        if (
            parse_open_browser_intent(text)
            or parse_open_target(text)
            or parse_download_search_query(text)
            or parse_web_search_query(text)
            or parse_compose_request(text)
            or looks_like_memory_panel_intent(text)
        ):
            return False
        return ctx.may_use_ollama

    @staticmethod
    def _should_dispatch_llm(text: str, ctx: SessionContext) -> bool:
        cleaned = " ".join((text or "").split())
        if len(cleaned) < 6:
            return False
        words = [w.strip(".,!?;:") for w in cleaned.lower().split()]
        if len(words) < 2:
            return False
        if all(word in ctx.voice_filler_words for word in words):
            return False
        if parse_fx_enabled(text) is not None:
            return False
        if parse_hide_intent(text) or parse_quit_intent(text) or looks_like_listen_off(text):
            return False
        if (
            parse_open_browser_intent(text)
            or parse_open_target(text)
            or parse_download_search_query(text)
            or parse_web_search_query(text)
            or parse_compose_request(text)
            or looks_like_memory_panel_intent(text)
        ):
            return False
        if has_add_intent(text):
            return False
        from quest_assistant.parser import parse_action

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
