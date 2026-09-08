from __future__ import annotations

from .models import ScoredHotel
from .scoring import rank_key


def select_top_three(scored: list[ScoredHotel]) -> list[tuple[str, ScoredHotel]]:
    ranked = sorted(scored, key=rank_key)
    if not ranked:
        return []

    best = ranked[0]
    used = {best["hotel_id"]}
    result: list[tuple[str, ScoredHotel]] = [("best_match", best)]

    value_pool = [
        hotel
        for hotel in ranked
        if hotel["hotel_id"] not in used and hotel["total_score"] >= 80
    ]
    if value_pool:
        value = min(value_pool, key=lambda hotel: (hotel["price_total"], *rank_key(hotel)))
        result.append(("best_value", value))
        used.add(value["hotel_id"])

    alternatives = [hotel for hotel in ranked if hotel["hotel_id"] not in used]
    if alternatives:
        result.append(("alternative", sorted(alternatives, key=rank_key)[0]))

    return result[:3]
