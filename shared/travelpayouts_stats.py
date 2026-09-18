"""Travelpayouts booking/revenue statistics for TurBot partner placements.

Uses the current statistics/v1 API. The integration deliberately filters by the
SubIDs TurBot controls instead of trying to infer user-level attribution.
"""
from __future__ import annotations

import copy
import os
import time
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, Iterable

import requests


STATS_URL = "https://api.travelpayouts.com/statistics/v1/execute_query"
DEFAULT_TIMEOUT = 10
DEFAULT_CACHE_TTL = 300
PAGE_LIMIT = 10_000

# Keep aliases forever. Renaming a SubID should not erase historical revenue
# from reports.
SUB_ID_TO_SERVICE = {
    "tg_hotels": "hotel",
    "tg_esim": "esim",
    "turbot_esim_tg": "esim",
    "tg_kiwitaxi_transfer": "transfer",
    "tg_transfer": "transfer",
}


class TravelpayoutsStatsError(RuntimeError):
    """Travelpayouts statistics could not be fetched or parsed."""


class TravelpayoutsStatsNotConfigured(TravelpayoutsStatsError):
    """Statistics API credentials are not configured or accepted."""


_cache: Dict[int, tuple[float, Dict[str, Any]]] = {}
_last_success_at: float = 0.0
_last_error_at: float = 0.0
_last_error_code: str = ""


def _cache_ttl() -> int:
    raw = os.getenv("TRAVELPAYOUTS_STATS_CACHE_TTL", "").strip()
    if not raw:
        return DEFAULT_CACHE_TTL
    try:
        return max(0, min(3600, int(raw)))
    except ValueError:
        return DEFAULT_CACHE_TTL


def _money(value: Any) -> Decimal:
    try:
        if value in (None, ""):
            return Decimal("0")
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return Decimal("0")


def _normalize_sub_id(value: Any) -> str:
    return str(value or "").strip().lstrip(".").lower()


def _latest_actions(rows: Iterable[Dict[str, Any]]) -> list[Dict[str, Any]]:
    """Deduplicate action updates, keeping the newest record per action_id."""
    latest: Dict[str, Dict[str, Any]] = {}
    anonymous: list[Dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        action_id = str(row.get("action_id") or "").strip()
        if not action_id:
            anonymous.append(row)
            continue
        previous = latest.get(action_id)
        if previous is None:
            latest[action_id] = row
            continue
        old_stamp = str(previous.get("updated_at") or previous.get("created_at") or "")
        new_stamp = str(row.get("updated_at") or row.get("created_at") or "")
        if new_stamp >= old_stamp:
            latest[action_id] = row
    return list(latest.values()) + anonymous


def _request_page(
    *,
    token: str,
    start_date: str,
    end_date: str,
    offset: int,
    timeout: int,
) -> Dict[str, Any]:
    body = {
        "fields": [
            "action_id",
            "sub_id",
            "price_eur",
            "paid_profit_eur",
            "state",
            "date",
            "updated_at",
            "created_at",
        ],
        "filters": [
            {"field": "date", "op": "ge", "value": start_date},
            {"field": "date", "op": "le", "value": end_date},
            {"field": "type", "op": "eq", "value": "action"},
        ],
        "sort": [{"field": "date", "order": "desc"}],
        "offset": offset,
        "limit": PAGE_LIMIT,
    }
    headers = {
        "X-Access-Token": token,
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    try:
        response = requests.post(
            STATS_URL,
            json=body,
            headers=headers,
            timeout=timeout,
        )
    except requests.RequestException as exc:
        raise TravelpayoutsStatsError("Travelpayouts statistics API is unavailable") from exc

    if response.status_code == 401:
        raise TravelpayoutsStatsNotConfigured("Travelpayouts API token is invalid")
    if response.status_code >= 400:
        raise TravelpayoutsStatsError(
            f"Travelpayouts statistics returned HTTP {response.status_code}"
        )

    try:
        payload = response.json()
    except ValueError as exc:
        raise TravelpayoutsStatsError("Travelpayouts statistics returned invalid JSON") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
        raise TravelpayoutsStatsError("Travelpayouts statistics returned an invalid response")
    return payload


def _fetch_rows(days: int, *, timeout: int) -> tuple[list[Dict[str, Any]], str, str]:
    token = os.getenv("TRAVELPAYOUTS_API_TOKEN", "").strip()
    if not token:
        raise TravelpayoutsStatsNotConfigured("TRAVELPAYOUTS_API_TOKEN is missing")

    today = date.today()
    start = today - timedelta(days=days - 1)
    start_date = start.isoformat()
    end_date = today.isoformat()

    rows: list[Dict[str, Any]] = []
    offset = 0
    while True:
        payload = _request_page(
            token=token,
            start_date=start_date,
            end_date=end_date,
            offset=offset,
            timeout=timeout,
        )
        page = payload.get("results") or []
        rows.extend(row for row in page if isinstance(row, dict))
        total_rows = int(payload.get("total_rows") or len(rows))
        offset += len(page)
        if not page or offset >= total_rows:
            break
        if len(page) < PAGE_LIMIT:
            break
    return rows, start_date, end_date


def _empty_service() -> Dict[str, Any]:
    return {
        "bookings": 0,
        "paid": 0,
        "processing": 0,
        "canceled": 0,
        "other": 0,
        "booking_value_eur": 0.0,
        "paid_profit_eur": 0.0,
    }


def _summarize(rows: Iterable[Dict[str, Any]], *, days: int, start_date: str, end_date: str) -> Dict[str, Any]:
    by_service = {
        "hotel": _empty_service(),
        "esim": _empty_service(),
        "transfer": _empty_service(),
    }
    matched = []
    for row in _latest_actions(rows):
        sub_id = _normalize_sub_id(row.get("sub_id"))
        service = SUB_ID_TO_SERVICE.get(sub_id)
        if not service:
            continue
        matched.append(row)
        bucket = by_service[service]
        bucket["bookings"] += 1

        state = str(row.get("state") or "").strip().lower()
        if state == "paid":
            bucket["paid"] += 1
        elif state == "processing":
            bucket["processing"] += 1
        elif state in {"canceled", "cancelled"}:
            bucket["canceled"] += 1
        else:
            bucket["other"] += 1

        # Booking value is useful operationally but is not revenue.
        if state not in {"canceled", "cancelled"}:
            bucket["booking_value_eur"] += float(_money(row.get("price_eur")))
        bucket["paid_profit_eur"] += float(_money(row.get("paid_profit_eur")))

    for bucket in by_service.values():
        bucket["booking_value_eur"] = round(bucket["booking_value_eur"], 2)
        bucket["paid_profit_eur"] = round(bucket["paid_profit_eur"], 2)

    totals = _empty_service()
    for bucket in by_service.values():
        for key in ("bookings", "paid", "processing", "canceled", "other"):
            totals[key] += bucket[key]
        totals["booking_value_eur"] += bucket["booking_value_eur"]
        totals["paid_profit_eur"] += bucket["paid_profit_eur"]
    totals["booking_value_eur"] = round(totals["booking_value_eur"], 2)
    totals["paid_profit_eur"] = round(totals["paid_profit_eur"], 2)

    return {
        "available": True,
        "days": days,
        "start_date": start_date,
        "end_date": end_date,
        "matched_rows": len(matched),
        "totals": totals,
        "by_service": by_service,
    }


def fetch_partner_performance(
    days: int = 30,
    *,
    timeout: int = DEFAULT_TIMEOUT,
    force: bool = False,
) -> Dict[str, Any]:
    """Fetch booking/revenue totals for TurBot SubIDs.

    The result is cached briefly because /partners is an admin report, not a
    reason to hammer the Travelpayouts API every time somebody taps Telegram's
    command twice.
    """
    try:
        days = int(days)
    except (TypeError, ValueError):
        days = 30
    days = max(1, min(365, days))

    ttl = _cache_ttl()
    cached = _cache.get(days)
    if not force and ttl > 0 and cached and cached[0] >= time.monotonic() - ttl:
        return copy.deepcopy(cached[1])

    global _last_success_at, _last_error_at, _last_error_code
    try:
        rows, start_date, end_date = _fetch_rows(days, timeout=timeout)
    except TravelpayoutsStatsError as exc:
        _last_error_at = time.time()
        _last_error_code = (
            "not_configured"
            if isinstance(exc, TravelpayoutsStatsNotConfigured)
            else "api_error"
        )
        raise

    result = _summarize(rows, days=days, start_date=start_date, end_date=end_date)
    _last_success_at = time.time()
    _last_error_code = ""
    if ttl > 0:
        _cache[days] = (time.monotonic(), copy.deepcopy(result))
    return result


def health_snapshot(*, now: float | None = None) -> Dict[str, Any]:
    """Return non-secret operational state without making a network request."""
    current = time.time() if now is None else float(now)
    configured = bool(os.getenv("TRAVELPAYOUTS_API_TOKEN", "").strip())
    if _last_success_at:
        status = "ok"
    elif _last_error_at:
        status = "error"
    else:
        status = "never"
    return {
        "configured": configured,
        "status": status,
        "cache_ttl_seconds": _cache_ttl(),
        "cached_windows": sorted(_cache),
        "last_success_age_seconds": (
            round(max(0.0, current - _last_success_at), 1)
            if _last_success_at
            else None
        ),
        "last_error_age_seconds": (
            round(max(0.0, current - _last_error_at), 1)
            if _last_error_at
            else None
        ),
        "last_error_code": _last_error_code or None,
    }


def clear_cache() -> None:
    """Clear in-memory statistics cache; health history remains intact."""
    _cache.clear()
