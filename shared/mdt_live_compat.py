"""Compatibility helpers for live MDT response shapes.

The production ``add-lead`` endpoint can create a lead successfully while
returning only a top-level ``result`` value and no numeric ``id``/``lead_id``.
Older code interpreted that as failure and retried, which duplicated leads.
Website and Telegram use the same live endpoint and have both exhibited this
response shape, so accept only the conservative success variants for those
sources. VK behaviour stays untouched because its delivery modes have separate
recovery/one-shot semantics.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from shared import mdt as mdt_shared

logger = logging.getLogger("turbot.shared.mdt_live_compat")

_ORIGINAL_CREATE_LEAD = mdt_shared.create_lead
_INSTALLED = False
_RESULT_ONLY_SOURCES = {"website", "telegram"}


def _live_result_is_success(response: Any) -> bool:
    """Return True for the success-only ``result`` shapes seen in live MDT.

    Explicit error/false shapes remain failures. We intentionally do not treat
    an arbitrary non-empty string as success because validation errors may be
    returned as text.
    """
    if not isinstance(response, dict) or "result" not in response:
        return False
    if any(response.get(key) for key in ("error", "errors", "exception")):
        return False

    value = response.get("result")
    if value is True:
        return True
    if value in (False, None, 0, "", "0"):
        return False
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return value > 0
    if isinstance(value, str):
        normalized = value.strip().casefold()
        return normalized in {"ok", "success", "true", "created", "done"}
    if isinstance(value, dict):
        if any(value.get(key) for key in ("error", "errors", "exception")):
            return False
        if value.get("success") is True or value.get("ok") is True:
            return True
        if mdt_shared.extract_id(value, "id", "lead_id") is not None:
            return True
    return False


def _source_accepts_result_only(settings: mdt_shared.MDTSettings) -> bool:
    """Limit ambiguous live-response compatibility to observed lead sources."""
    return settings.name_prefix.strip().casefold() in _RESULT_ONLY_SOURCES


def install() -> None:
    """Patch create_lead once for observed live result-only success responses."""
    global _INSTALLED
    if _INSTALLED:
        return

    def create_lead_compat(
        settings: mdt_shared.MDTSettings,
        chat_id: int,
        info: Dict[str, Any],
        phone: str,
        client_name: Optional[str],
        request_fn: mdt_shared.RequestFn,
        log: Optional[logging.Logger] = None,
    ) -> bool:
        captured: Dict[str, Any] = {}

        def request_capture(method: str, params: Dict[str, Any]):
            response = request_fn(method, params)
            if method == "add-lead":
                captured["response"] = response
            return response

        ok = _ORIGINAL_CREATE_LEAD(
            settings,
            chat_id,
            info,
            phone,
            client_name,
            request_capture,
            log=log,
        )
        if ok:
            return True

        if not _source_accepts_result_only(settings):
            return False

        if _live_result_is_success(captured.get("response")):
            source = settings.name_prefix.strip() or "Bot"
            (log or logger).info(
                "MDT add-lead accepted %s lead %s via result-only response",
                source,
                chat_id,
            )
            return True
        return False

    mdt_shared.create_lead = create_lead_compat
    _INSTALLED = True
