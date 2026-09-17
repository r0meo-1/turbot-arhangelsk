"""Production WSGI entrypoint for the VK bot.

Importing ``vk_bot`` starts its background workers. Runtime guards are installed
immediately after import, before customer messages reach the Flask callback.
"""
from __future__ import annotations

import vk_bot as _bot

from shared.vk_followup import install as _install_followup_guard
from shared.vk_booking_support import install as _install_booking_support


_install_followup_guard(_bot)
_install_booking_support(_bot)

app = _bot.app


@app.get("/vk/runtime")
def runtime_status():
    """Small, PII-free production probe for the guarded VK runtime."""
    return {
        "ok": True,
        "runtime": "vk",
        "followup": "durable-v1",
        "guardInstalled": bool(getattr(_bot, "_durable_followup_installed", False)),
        "bookingSupport": "v1",
        "bookingSupportInstalled": bool(getattr(_bot, "_booking_support_installed", False)),
    }
