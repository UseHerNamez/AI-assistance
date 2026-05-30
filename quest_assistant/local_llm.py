from __future__ import annotations

import json
import os
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Optional
from urllib.parse import urlparse

from quest_assistant.hardware_profile import HardwareProfile, detect_hardware_profile
from quest_assistant.parser import normalize_quest_title


DEFAULT_MODEL = os.environ.get("JARVIS_LLM_MODEL", "qwen2.5:3b")
_DEFAULT_LOCAL_ENDPOINT = "http://127.0.0.1:11434"
_LOCAL_OLLAMA_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


def resolve_local_ollama_endpoint(raw: str | None = None) -> tuple[str, Optional[str]]:
    """
    Keep LLM traffic on this machine. Remote OLLAMA_HOST values are rejected so
    quest titles and utterances cannot be sent to an arbitrary server.
    """
    endpoint = (raw or os.environ.get("OLLAMA_HOST", _DEFAULT_LOCAL_ENDPOINT)).strip().rstrip("/")
    parsed = urlparse(endpoint)
    if parsed.scheme not in {"http", "https"}:
        return _DEFAULT_LOCAL_ENDPOINT, "OLLAMA_HOST must use http://127.0.0.1:11434 (invalid URL)"
    host = (parsed.hostname or "").lower()
    if host not in _LOCAL_OLLAMA_HOSTS:
        return _DEFAULT_LOCAL_ENDPOINT, "OLLAMA_HOST must point to this PC (127.0.0.1 only)"
    return endpoint, None


DEFAULT_ENDPOINT = resolve_local_ollama_endpoint()[0]


@dataclass(frozen=True)
class LLMAction:
    kind: str
    title: Optional[str] = None
    value: Optional[str] = None


@dataclass(frozen=True)
class LLMResult:
    actions: list[LLMAction]
    elapsed_s: float
    model: str


class LocalLLMInterpreter:
    """
    Free local command understanding through Ollama.

    If the local model is unavailable or too slow for this machine, callers can
    fall back to the deterministic parser.
    """

    def __init__(
        self,
        *,
        model: str = DEFAULT_MODEL,
        endpoint: str = DEFAULT_ENDPOINT,
        timeout_s: float = 5.0,
        max_good_latency_s: float = 3.5,
    ) -> None:
        resolved, endpoint_error = resolve_local_ollama_endpoint(endpoint)
        self.model = model
        self.endpoint = resolved.rstrip("/")
        self._endpoint_error = endpoint_error
        self.timeout_s = timeout_s
        self.max_good_latency_s = max_good_latency_s
        self.hardware: Optional[HardwareProfile] = None
        # Until hardware is probed (and the model warmed) in the background, keep
        # the LLM "disabled" so startup is instant and the deterministic parser
        # handles any early commands without blocking the UI thread.
        self.disabled_reason: Optional[str] = "starting up"
        self._disabled_until = float("inf")
        self._slow_count = 0
        self._fail_count = 0
        self._hw_ready = False
        self._warmed = False
        threading.Thread(target=self._startup_probe, daemon=True).start()

    def _startup_probe(self) -> None:
        if self._endpoint_error:
            self.disabled_reason = self._endpoint_error
            self._disabled_until = float("inf")
            self._hw_ready = True
            return
        try:
            hw = detect_hardware_profile()
        except Exception:
            hw = None
        self.hardware = hw

        if hw is None:
            self.disabled_reason = "hardware detection failed"
            self._disabled_until = float("inf")
            self._hw_ready = True
            return
        if not hw.allow_llm:
            self.disabled_reason = hw.reason
            self._disabled_until = float("inf")
            self._hw_ready = True
            return

        self.disabled_reason = None
        self._disabled_until = 0.0
        self._hw_ready = True
        self._warmup()

    def _warmup(self) -> None:
        # Load the model into memory so the first real command is fast instead of
        # paying the multi-second cold-start latency mid-conversation.
        if self._warmed:
            return
        try:
            payload = {
                "model": self.model,
                "stream": False,
                "keep_alive": "30m",
                "messages": [{"role": "user", "content": "ok"}],
                "options": {"num_predict": 1, "temperature": 0.0},
            }
            req = urllib.request.Request(
                f"{self.endpoint}/api/chat",
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=60) as response:
                response.read()
            self._warmed = True
        except Exception:
            self._warmed = False

    @property
    def is_enabled(self) -> bool:
        if not self._hw_ready:
            return False
        if self.disabled_reason and self._disabled_until != float("inf") and time.monotonic() >= self._disabled_until:
            self.disabled_reason = None
            self._slow_count = 0
            self._fail_count = 0
        return self.disabled_reason is None

    @property
    def may_use_ollama(self) -> bool:
        """Hardware allows Ollama; ignores temporary backoff from slow/failed calls."""
        if not self._hw_ready or self._endpoint_error:
            return False
        if self.hardware is None:
            return False
        if self.hardware.allow_llm:
            return True
        return os.environ.get("JARVIS_LLM_MODE", "").strip().lower() == "force"

    _ACTION_KINDS = frozenset(
        {
            "add",
            "complete",
            "delete",
            "edit",
            "set_fx",
            "show",
            "hide",
            "listen_off",
            "quit",
            "open_browser",
            "web_search",
            "reply",
        }
    )

    def interpret(
        self,
        text: str,
        *,
        pending_add: bool = False,
        jarvis_awake: bool = False,
        open_quests: Optional[list[dict[str, object]]] = None,
        source: str = "voice",
    ) -> Optional[LLMResult]:
        if not self.is_enabled and not (source == "voice" and jarvis_awake and self.may_use_ollama):
            return None

        # Calls run off the UI thread, so we can afford a generous timeout that
        # tolerates an occasional slow inference without freezing anything.
        timeout_s = 12.0 if source == "voice" else 8.0
        max_good = self.max_good_latency_s

        started = time.perf_counter()
        try:
            payload = self._build_payload(
                text,
                pending_add=pending_add,
                jarvis_awake=jarvis_awake,
                open_quests=open_quests or [],
            )
            outer = self._post_chat(payload, timeout_s=timeout_s)
            raw = json.dumps(outer)
        except urllib.error.URLError as exc:
            reason = str(getattr(exc, "reason", exc)).lower()
            if "refused" in reason or "could not" in reason or "no connection" in reason:
                # Ollama is not running at all; back off briefly, do not spam it.
                self._disable_temporarily("local model not running", cooldown_s=30.0)
            else:
                self._note_failure()
            return None
        except (TimeoutError, OSError):
            self._note_failure()
            return None

        self._fail_count = 0
        elapsed = time.perf_counter() - started
        if elapsed > max_good:
            self._slow_count += 1
            if self._slow_count >= 4:
                self._disable_temporarily(f"local LLM too slow ({elapsed:.1f}s)", cooldown_s=120.0)
        else:
            self._slow_count = 0

        try:
            outer = json.loads(raw) if isinstance(raw, str) else raw
            content = outer.get("message", {}).get("content", "")
            parsed = _loads_json_object(content)
            actions = [
                LLMAction(
                    kind=str(a.get("kind", "noop")),
                    title=_clean_title(a.get("title")),
                    value=_clean_value(a.get("value")),
                )
                for a in parsed.get("actions", [])
                if isinstance(a, dict)
            ]
        except (TypeError, ValueError, json.JSONDecodeError):
            return None

        if not actions:
            return None
        return LLMResult(actions=actions, elapsed_s=elapsed, model=self.model)

    def interpret_voice(
        self,
        text: str,
        *,
        pending_add: bool = False,
        jarvis_awake: bool = False,
        open_quests: Optional[list[dict[str, object]]] = None,
    ) -> Optional[LLMResult]:
        """
        Command JSON first; if Jarvis is awake and nothing useful came back, free chat.
        """
        result = self.interpret(
            text,
            pending_add=pending_add,
            jarvis_awake=jarvis_awake,
            open_quests=open_quests,
            source="voice",
        )
        if result and self._has_useful_actions(result.actions):
            return result
        if not jarvis_awake or not self.may_use_ollama:
            return result
        reply = self.converse(text, jarvis_awake=True)
        if not reply:
            return result
        return LLMResult(
            actions=[LLMAction(kind="reply", value=reply)],
            elapsed_s=result.elapsed_s if result else 0.0,
            model=self.model,
        )

    def converse(self, text: str, *, jarvis_awake: bool = False) -> Optional[str]:
        """Plain conversational reply — no JSON action schema."""
        if not self.may_use_ollama:
            return None
        started = time.perf_counter()
        mood = (
            "You are awake and listening. Answer naturally in one or two short sentences."
            if jarvis_awake
            else "Answer briefly if the user needs help with quests or controls."
        )
        system = (
            "You are Jarvis from Iron Man — concise, capable, respectful (address the user as sir). "
            + mood
            + " Do not invent quest actions; just converse."
        )
        payload = {
            "model": self.model,
            "stream": False,
            "keep_alive": "30m",
            "options": {"temperature": 0.4, "num_predict": 120},
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": text},
            ],
        }
        try:
            raw = self._post_chat(payload, timeout_s=14.0)
        except urllib.error.URLError as exc:
            reason = str(getattr(exc, "reason", exc)).lower()
            if "refused" in reason or "could not" in reason or "no connection" in reason:
                self._disable_temporarily("local model not running", cooldown_s=30.0)
            else:
                self._note_failure()
            return None
        except (TimeoutError, OSError):
            self._note_failure()
            return None

        self._fail_count = 0
        elapsed = time.perf_counter() - started
        if elapsed > self.max_good_latency_s:
            self._slow_count += 1
        else:
            self._slow_count = 0

        content = raw.get("message", {}).get("content", "")
        reply = (content or "").strip()
        if reply.startswith("```"):
            reply = reply.strip("`").removeprefix("json").strip()
        return reply or None

    @staticmethod
    def _has_useful_actions(actions: list[LLMAction]) -> bool:
        for action in actions:
            if action.kind == "noop":
                continue
            if action.kind == "reply" and action.value:
                return True
            if action.kind in LocalLLMInterpreter._ACTION_KINDS - {"reply"}:
                return True
        return False

    def _post_chat(self, payload: dict[str, Any], *, timeout_s: float) -> dict[str, Any]:
        req = urllib.request.Request(
            f"{self.endpoint}/api/chat",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout_s) as response:
            raw = response.read().decode("utf-8", errors="replace")
        return json.loads(raw)

    def _disable_temporarily(self, reason: str, *, cooldown_s: float) -> None:
        self.disabled_reason = reason
        self._disabled_until = time.monotonic() + cooldown_s

    def _note_failure(self) -> None:
        # Tolerate the occasional slow/cold response; only back off after a few
        # consecutive failures so one timeout does not knock the LLM offline.
        self._fail_count += 1
        if self._fail_count >= 2:
            self._fail_count = 0
            self._disable_temporarily("local LLM unresponsive", cooldown_s=30.0)

    def _build_payload(
        self,
        text: str,
        *,
        pending_add: bool,
        jarvis_awake: bool,
        open_quests: list[dict[str, object]],
    ) -> dict[str, Any]:
        # Keep this prompt SHORT. On modest hardware, prompt length dominates
        # latency, so a compact instruction keeps responses near ~1.5-3s.
        mood = (
            "You are awake and listening. Infer intent from casual speech — no exact phrases needed. "
            "Prefer reply for chat, but act when the user wants something done."
            if jarvis_awake
            else "Ignore idle chatter unless the user clearly wants Assistance."
        )
        system = (
            "You are Jarvis from Iron Man — concise, capable, respectful (sir). Output JSON only. "
            + mood
            + " "
            "Map utterances to actions. Kinds: "
            "set_fx(value on|off); show; hide (never quit); "
            "listen_off for mute mic / stop listening / privacy / go silent; "
            "quit only for shut down/exit; "
            "add(title); edit(title old ref, value new title); "
            "complete(title or task number); "
            "delete(title or number) ONLY when the user clearly asks to delete/remove; "
            "open_browser; web_search(value query); "
            "reply(value) for ANY conversation, greeting, or question (e.g. are you here, how are you); "
            "noop only if you cannot infer anything. "
            "open_quests lists numbered tasks. "
            'Return {"actions":[{"kind":"...","title":null,"value":null}]}.'
        )
        user = json.dumps(
            {
                "pending_add": pending_add,
                "open_quests": open_quests,
                "utterance": text,
            }
        )
        return {
            "model": self.model,
            "stream": False,
            "format": "json",
            "keep_alive": "30m",
            "options": {"temperature": 0.0, "num_predict": 96},
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }


def _loads_json_object(content: str) -> dict[str, Any]:
    content = (content or "").strip()
    if content.startswith("```"):
        content = content.strip("`")
        content = content.removeprefix("json").strip()
    return json.loads(content)


def _clean_title(value: Any) -> Optional[str]:
    if value is None:
        return None
    title = normalize_quest_title(str(value))
    return title or None


def _clean_value(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None

