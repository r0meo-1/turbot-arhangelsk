"""Privacy-safe aggregate runtime telemetry helpers."""

from __future__ import annotations

import math
from typing import Any, Dict, Iterable, Optional, Tuple

TimestampPair = Tuple[Optional[int], Optional[int]]


def _percentile(values: list[int], percentile: float) -> Optional[int]:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, math.ceil((len(ordered) - 1) * percentile))
    return int(ordered[index])


def lead_delivery_snapshot(
    rows: Iterable[TimestampPair], *, window_seconds: int
) -> Dict[str, Any]:
    """Summarize durable-create to manager-delivery latency without PII."""
    accepted = 0
    pending = 0
    latencies: list[int] = []
    for created_at, notified_at in rows:
        if created_at is None:
            continue
        accepted += 1
        if notified_at is None:
            pending += 1
        else:
            latencies.append(max(0, int(notified_at) - int(created_at)))
    return {
        "window_seconds": int(window_seconds),
        "accepted": accepted,
        "manager_notified": len(latencies),
        "pending_manager_delivery": pending,
        "latency_seconds": {
            "p50": _percentile(latencies, 0.50),
            "p95": _percentile(latencies, 0.95),
            "p99": _percentile(latencies, 0.99),
        },
    }


def event_counter_snapshot(
    rows: Iterable[Tuple[str, str, int]], *, window_seconds: int
) -> Dict[str, Any]:
    """Convert grouped subject/outcome counts into a bounded safe payload."""
    subjects: Dict[str, Dict[str, int]] = {}
    for subject, outcome, count in rows:
        safe_subject = str(subject or "unknown")[:40]
        safe_outcome = str(outcome or "unknown")[:40]
        subjects.setdefault(safe_subject, {})[safe_outcome] = int(count or 0)
    return {"window_seconds": int(window_seconds), "subjects": subjects}
