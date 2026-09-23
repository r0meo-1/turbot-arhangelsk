"""UTC clock for the existing naive-UTC CRM boundary.

CRM storage returns naive UTC via _from_epoch, and existing domain comparisons
and JSON consumers use that representation. Keep it stable during warning
cleanup; a timezone-aware domain migration is a separate compatibility change.
Never call timestamp() on this value without first attaching timezone.utc.
"""

from datetime import datetime, timezone


def utc_now_naive() -> datetime:
    """Return naive UTC without invoking Python's deprecated UTC factories."""
    return datetime.now(timezone.utc).replace(tzinfo=None)
