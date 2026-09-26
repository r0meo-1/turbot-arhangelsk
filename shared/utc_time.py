"""UTC clock helpers preserving TurBot's existing naive-UTC contract."""

from __future__ import annotations

from datetime import datetime, timezone


def utc_now_naive() -> datetime:
    """Return current UTC time as a naive datetime without using utcnow()."""
    return datetime.now(timezone.utc).replace(tzinfo=None)
