from __future__ import annotations

import json
import os
import threading
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
        timeout_s: float = 5.0,
        max_good_latency_s: float = 3.5,
    ) -> None:
        self.model = model
        self.endpoint = endpoint.rstrip("/")
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

    def interpret(
        self,
        text: str,
        *,
        pending_add: bool = False,
        jarvis_awake: bool = False,
        open_quests: Optional[list[dict[str, object]]] = None,
        source: str = "voice",
    ) -> Optional[LLMResult]:
        if not self.is_enabled:
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
            req = urllib.request.Request(
                f"{self.endpoint}/api/chat",
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=timeout_s) as response:
                raw = response.read().decode("utf-8", errors="replace")
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
        mood = "Reply to small talk." if jarvis_awake else "Ignore idle chatter."
        system = (
            "You are Jarvis. Output JSON only, no prose. " + mood + " "
            "Map casual speech to one action. Kinds: "
            "set_fx(value on|off) for glow/effects/animations/visuals; "
            "show; hide for close/go away/disappear/minimize (never quit); "
            "listen_off for mute/privacy/stop listening; "
            "quit for shut down/exit/quit; "
            "add(title) to create a quest/task; "
            "complete(title) to finish one (use the number for 'task 2'); "
            "delete(title) to remove; reply(value) for chat; noop if unclear. "
            "open_quests lists current tasks by number. "
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

