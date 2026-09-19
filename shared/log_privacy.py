"""Privacy-safe correlation identifiers for routine operational logs."""

from __future__ import annotations

import hashlib
import hmac
import os
from typing import Any


def correlation_id(value: Any, *, namespace: str = "user") -> str:
    """Return a stable pseudonymous log reference when a dedicated key exists.

    Without LOG_CORRELATION_KEY we fail closed to a non-identifying marker
    rather than hashing a platform ID with a public/guessable algorithm.
    """
    ns = "".join(ch for ch in str(namespace or "user").casefold() if ch.isalnum() or ch in "-_")[:24] or "user"
    raw = str(value or "").strip()
    if not raw:
        return f"{ns}:none"

    key = os.getenv("LOG_CORRELATION_KEY", "").strip()
    if not key:
        return f"{ns}:redacted"

    digest = hmac.new(
        key.encode("utf-8"),
        f"{ns}:{raw}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()[:12]
    return f"{ns}:{digest}"
