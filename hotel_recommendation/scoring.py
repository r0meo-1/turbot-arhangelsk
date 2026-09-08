from __future__ import annotations

from typing import Any

from .config import WEIGHTS
from .models import HotelCandidate, ScoredHotel


def score_candidate(hotel: HotelCandidate) -> ScoredHotel:
    breakdown = {key: round(hotel["signals"][key] * weight / 100) for key, weight in WEIGHTS.items()}
    return {**hotel, "score_breakdown": breakdown, "total_score": sum(breakdown.values())}


def rank_key(hotel: ScoredHotel) -> tuple[Any, ...]:
    return (
        -hotel["total_score"],
        -hotel["score_breakdown"]["reviews"],
        -hotel["tripadvisor_review_count"],
        hotel["hotel_id"],
    )
