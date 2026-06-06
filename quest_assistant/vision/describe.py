from __future__ import annotations

import base64
import json
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional

from quest_assistant.local_llm import resolve_local_ollama_endpoint

_DEFAULT_VISION_MODEL = os.environ.get("JARVIS_VISION_MODEL", "llava")


def describe_screenshot(image_path: Path, *, user_prompt: str, timeout_s: float = 45.0) -> Optional[str]:
    """Send a local screenshot to Ollama vision model (CPU-heavy)."""
    endpoint, err = resolve_local_ollama_endpoint()
    if err:
        return None
    try:
        raw_bytes = image_path.read_bytes()
    except OSError:
        return None
    b64 = base64.b64encode(raw_bytes).decode("ascii")
    payload = {
        "model": _DEFAULT_VISION_MODEL,
        "stream": False,
        "keep_alive": "10m",
        "options": {"temperature": 0.2, "num_predict": 180},
        "messages": [
            {
                "role": "user",
                "content": user_prompt,
                "images": [b64],
            }
        ],
    }
    req = urllib.request.Request(
        f"{endpoint.rstrip('/')}/api/chat",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as response:
            outer = json.loads(response.read().decode("utf-8", errors="replace"))
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError):
        return None
    content = outer.get("message", {}).get("content", "")
    reply = (content or "").strip()
    return reply or None
