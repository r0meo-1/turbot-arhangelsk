from __future__ import annotations

from .config import WEIGHTS
from .models import HotelCandidate


def normalize_candidate(raw: HotelCandidate) -> HotelCandidate:
    signals = raw["signals"]
    if set(signals) != set(WEIGHTS):
        raise ValueError(f"invalid signals for {raw['hotel_id']}")
    return {
        "hotel_id": str(raw["hotel_id"]),
        "hotel_name": str(raw["hotel_name"]),
        "price_total": int(raw["price_total"]),
        "currency": str(raw.get("currency", "RUB")),
        "beach_distance_m": int(raw["beach_distance_m"]),
        "family_friendly": bool(raw["family_friendly"]),
        "breakfast": bool(raw["breakfast"]),
        "tripadvisor_rating": float(raw["tripadvisor_rating"]),
        "tripadvisor_review_count": int(raw["tripadvisor_review_count"]),
        "signals": {k: max(0, min(100, int(v))) for k, v in signals.items()},
    }
