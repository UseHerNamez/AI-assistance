from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ComposeRequest:
    """Voice request to draft text and open it in an app."""

    destination: str  # notepad | word | outlook
    topic: str
    raw: str = ""
