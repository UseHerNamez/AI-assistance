from __future__ import annotations

import os
import re


_VISION_RE = re.compile(
    r"\b(?:"
    r"what(?:'s| is) on (?:my )?screen|"
    r"look at (?:my )?screen|"
    r"describe (?:my |the )?screen|"
    r"what do you see|"
    r"read (?:my |the )?screen"
    r")\b",
    re.IGNORECASE,
)


def vision_enabled() -> bool:
    raw = os.environ.get("JARVIS_VISION", "0").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def looks_like_vision_request(text: str) -> bool:
    if not vision_enabled():
        return False
    return bool(_VISION_RE.search(text or ""))


def vision_user_prompt(text: str) -> str:
    cleaned = " ".join((text or "").split()).strip()
    return cleaned or "Describe what is on the screen briefly."
