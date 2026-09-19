"""AI provider selection for tour blurbs.

The public lead funnel must keep working if an external model is unavailable.
Provider configuration is therefore explicit and fail-closed: unknown modes,
missing credentials, or unsafe endpoints return no client and the caller falls
back to deterministic templates.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

try:
    from groq import Groq
except ImportError:  # pragma: no cover - optional at import time
    Groq = None  # type: ignore

try:
    from openai import OpenAI
except ImportError:  # pragma: no cover - optional at import time
    OpenAI = None  # type: ignore


@dataclass(frozen=True)
class AISelectionProvider:
    mode: str
    client: Any = None
    model: str = ""

    @property
    def ready(self) -> bool:
        return self.client is not None and bool(self.model)


def _https_base_url(value: str) -> str:
    """Return a normalized HTTPS API base URL or an empty string."""
    raw = (value or "").strip().rstrip("/")
    if not raw:
        return ""
    parsed = urlparse(raw)
    if parsed.scheme != "https" or not parsed.netloc:
        return ""
    return raw


def build_selection_provider(
    mode: str,
    *,
    groq_api_key: str = "",
    groq_model: str = "openai/gpt-oss-120b",
    regcloud_api_key: str = "",
    regcloud_base_url: str = "",
    regcloud_model: str = "",
) -> AISelectionProvider:
    """Build the explicitly selected provider for destination blurbs."""
    selected = (mode or "template").strip().lower()

    if selected == "template":
        return AISelectionProvider("template")

    if selected == "groq":
        key = (groq_api_key or "").strip()
        model = (groq_model or "").strip()
        if not key or not model or Groq is None:
            return AISelectionProvider("groq")
        return AISelectionProvider("groq", Groq(api_key=key), model)

    if selected == "regcloud":
        key = (regcloud_api_key or "").strip()
        base_url = _https_base_url(regcloud_base_url)
        model = (regcloud_model or "").strip()
        if not key or not base_url or not model or OpenAI is None:
            return AISelectionProvider("regcloud")
        return AISelectionProvider(
            "regcloud",
            OpenAI(api_key=key, base_url=base_url),
            model,
        )

    return AISelectionProvider(selected)
