"""Adapters from the current Telegram/VK funnel dictionaries to TripRequest.

The existing bot FSM remains the source of truth during qualification. This
module mirrors completed leads into the richer CRM model so rollout can be
incremental instead of requiring a risky funnel rewrite.
"""

from __future__ import annotations

import re
from typing import Any, Mapping

from shared.travel_crm import (
    Attribution,
    BudgetScope,
    BudgetType,
    Child,
    TripRequest,
)


def _int_or(value: Any, default: int) -> int:
    try:
        text = str(value).strip().rstrip("+")
        return int(text)
    except (TypeError, ValueError):
        return default


def _optional_positive_int(value: Any) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _ages(info: Mapping[str, Any]) -> tuple[Child, ...]:
    result: list[Child] = []
    for value in info.get("kids_ages") or ():
        try:
            result.append(Child(int(value)))
        except (TypeError, ValueError):
            continue
    return tuple(result)


def _nights(value: Any) -> tuple[int | None, int | None]:
    if value is None:
        return None, None
    if isinstance(value, int):
        return (value, value) if value > 0 else (None, None)

    raw = str(value).strip()
    if not raw:
        return None, None
    nums = [int(item) for item in re.findall(r"\d{1,2}", raw)]
    nums = [item for item in nums if item > 0]
    if not nums:
        return None, None
    if len(nums) == 1:
        return nums[0], nums[0]
    return min(nums[0], nums[1]), max(nums[0], nums[1])


def _budget_type(info: Mapping[str, Any]) -> BudgetType:
    raw = str(info.get("budget_type") or "").strip().lower()
    if raw in {item.value for item in BudgetType}:
        return BudgetType(raw)
    # Current Telegram/VK buttons are all phrased as "до N ₽", therefore the
    # legacy integer represents a ceiling, not a promise to spend exactly N.
    return BudgetType.MAX


def trip_request_from_lead(
    *,
    lead_id: int,
    info: Mapping[str, Any],
    channel: str,
) -> TripRequest:
    """Build a canonical request from a completed legacy lead dictionary."""

    channel = channel.strip().lower()
    if channel not in {"telegram", "vk", "website"}:
        raise ValueError("channel must be telegram, vk or website")

    nights_min, nights_max = _nights(info.get("nights"))
    dates_text = str(info.get("dates") or "").strip()
    source_tag = str(
        info.get("source_tag")
        or (info.get("utm_campaign") if channel == "website" else "")
        or ""
    ).strip()

    hotel_refs: tuple[str, ...] = ()
    hotel_query = str(info.get("hotel_query") or "").strip()
    if hotel_query:
        hotel_refs = (hotel_query,)

    scope = (
        BudgetScope.TOTAL
        if str(info.get("budget_scope") or "").strip().lower() == "total"
        else BudgetScope.PER_PERSON
    )

    return TripRequest(
        request_id=f"{'tg' if channel == 'telegram' else ('vk' if channel == 'vk' else 'web')}-lead-{int(lead_id)}",
        departure_city=str(info.get("origin") or "Не указан").strip() or "Не указан",
        adults=max(1, _int_or(info.get("people"), 1)),
        children=_ages(info),
        dates_text=dates_text,
        flexible_dates="гиб" in dates_text.lower(),
        nights_min=nights_min,
        nights_max=nights_max,
        budget_amount=_optional_positive_int(info.get("budget")),
        budget_type=_budget_type(info),
        budget_scope=scope,
        direct_only=bool(info.get("direct_only")),
        primary_destination=str(info.get("destination") or "").strip(),
        hotel_references=hotel_refs,
        special_wishes=(
            ("нужна консультация",) if bool(info.get("needs_consultation")) else ()
        ),
        attribution=Attribution(
            source_tag=source_tag,
            channel=channel,
            source=str(
                info.get("source")
                or (info.get("utm_source") if channel == "website" else "")
                or ""
            ).strip(),
            referrer=str(info.get("vk_ref") or info.get("referrer") or "").strip(),
            campaign=str(
                info.get("campaign")
                or (info.get("utm_campaign") if channel == "website" else "")
                or ""
            ).strip(),
        ),
    )
