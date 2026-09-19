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
