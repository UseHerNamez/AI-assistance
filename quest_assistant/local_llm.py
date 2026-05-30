from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Optional

from quest_assistant.hardware_profile import HardwareProfile, detect_hardware_profile
from quest_assistant.parser import normalize_quest_title


DEFAULT_MODEL = os.environ.get("JARVIS_LLM_MODEL", "qwen2.5:3b")
DEFAULT_ENDPOINT = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434")


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
        timeout_s: float = 3.5,
        max_good_latency_s: float = 2.8,
    ) -> None:
        self.model = model
        self.endpoint = endpoint.rstrip("/")
        self.timeout_s = timeout_s
        self.max_good_latency_s = max_good_latency_s
        self.hardware: HardwareProfile = detect_hardware_profile()
        self.disabled_reason: Optional[str] = None
        self._disabled_until = 0.0
        self._slow_count = 0
        if not self.hardware.allow_llm:
            self.disabled_reason = self.hardware.reason
            self._disabled_until = float("inf")

    @property
    def is_enabled(self) -> bool:
        if self.disabled_reason and time.monotonic() >= self._disabled_until:
            self.disabled_reason = None
            self._slow_count = 0
        return self.disabled_reason is None

    def interpret(
        self,
        text: str,
        *,
        pending_add: bool = False,
        jarvis_awake: bool = False,
    ) -> Optional[LLMResult]:
        if not self.is_enabled:
            return None

        started = time.perf_counter()
        try:
            payload = self._build_payload(text, pending_add=pending_add, jarvis_awake=jarvis_awake)
            req = urllib.request.Request(
                f"{self.endpoint}/api/chat",
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=self.timeout_s) as response:
                raw = response.read().decode("utf-8", errors="replace")
        except (TimeoutError, urllib.error.URLError, OSError):
            self._disable_temporarily("local LLM unavailable", cooldown_s=60.0)
            return None

        elapsed = time.perf_counter() - started
        if elapsed > self.max_good_latency_s:
            self._slow_count += 1
            if self._slow_count >= 2:
                self._disable_temporarily(f"local LLM too slow ({elapsed:.1f}s)", cooldown_s=300.0)
        else:
            self._slow_count = 0

        try:
            outer = json.loads(raw)
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

    def _disable_temporarily(self, reason: str, *, cooldown_s: float) -> None:
        self.disabled_reason = reason
        self._disabled_until = time.monotonic() + cooldown_s

    def _build_payload(self, text: str, *, pending_add: bool, jarvis_awake: bool) -> dict[str, Any]:
        awake_hint = (
            "Jarvis is awake and visible. The user may talk without saying 'Jarvis'. "
            "Use reply for casual conversation, greetings, comments, and general questions. "
            "Do not use noop for normal chit-chat while awake. "
            if jarvis_awake
            else "Jarvis is hidden/asleep. Only act on clear commands. Casual statements are noop unless they are clear commands. "
        )
        system = (
            "You are Jarvis, a local desktop quest assistant. Convert the user's natural "
            "English command into JSON only. Do not explain. "
            f"{awake_hint}"
            "Allowed action kinds: show, hide, listen_off, add_done, quit, add, complete, delete, "
            "set_fx, open_browser, web_search, reply, noop. "
            "Use add for quests/missions/tasks to create. Use complete for marking done. "
            "Use delete for removal. Use show/hide for opening or hiding the widget. "
            "Use set_fx with value 'on' or 'off' for visual/FX/animation requests. "
            "Use open_browser to open the default browser. Use web_search with value as the search query. "
            "Use reply for casual conversation, opinions, greetings, or when no safe computer action applies. "
            "Use listen_off for privacy mode, mute, turn off the mic/microphone, or stopping listening. "
            "Important: 'wake up', 'open up', 'show yourself', and 'appear' mean show, never listen_off. "
            "'stop listening', 'mute', 'privacy mode', 'turn off the mic', and 'mic off' mean listen_off. "
            "If the user says they want to add a quest but gives no title, output add with title null. "
            "Only output add when the user clearly asks to add, create, record, log, or write down a quest, "
            "mission, or task, or when pending_add is true. "
            "If pending_add is true, treat the user's words as quest titles unless they clearly stop adding "
            "or ask a question. Questions like 'why did you add that' or 'what did you do' must be noop. "
            "Never create quests from open/show/hide/delete/why/what/how questions. "
            "For reply actions, keep value short: one or two friendly sentences. "
            "Split multiple quests into multiple add actions. Keep titles short and imperative, e.g. "
            "'wash dishes', 'clean the house', 'work out', 'study math'. "
            "Return exactly this JSON shape: {\"actions\":[{\"kind\":\"reply\",\"value\":\"Of course, sir.\"}]}."
        )
        user = json.dumps({"pending_add": pending_add, "jarvis_awake": jarvis_awake, "utterance": text})
        return {
            "model": self.model,
            "stream": False,
            "format": "json",
            "keep_alive": "30m",
            "options": {"temperature": 0.0, "num_predict": 220},
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

