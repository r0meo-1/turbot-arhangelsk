"""Production WSGI entrypoint for the VK bot.

Importing ``vk_bot`` starts its background workers. The follow-up worker sleeps
before its first pass, so installing the durable guard immediately after import
replaces the callable before any reminder can be sent.
"""
from __future__ import annotations

import vk_bot as _bot

from shared.vk_followup import install as _install_followup_guard


_install_followup_guard(_bot)

app = _bot.app


@app.get("/vk/runtime")
def runtime_status():
    """Small, PII-free production probe for the guarded VK runtime."""
    return {
        "ok": True,
        "runtime": "vk",
        "followup": "durable-v1",
        "guardInstalled": bool(getattr(_bot, "_durable_followup_installed", False)),
    }
