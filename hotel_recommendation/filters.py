from __future__ import annotations

from .models import HotelCandidate, SearchRequest


def evaluate_hard_filters(request: SearchRequest, hotel: HotelCandidate) -> list[str]:
    requirements = request.get("mandatory_requirements", {})
    failed: list[str] = []
    if hotel["currency"] != request["currency"]:
        failed.append("CURRENCY_MISMATCH")
    if hotel["price_total"] > request["budget_total"]:
        failed.append("OVER_BUDGET")
    if requirements.get("family") and not hotel["family_friendly"]:
        failed.append("FAMILY_NOT_SUPPORTED")
    if requirements.get("breakfast") and not hotel["breakfast"]:
        failed.append("REQUIRED_BREAKFAST_MISSING")
    if hotel["beach_distance_m"] > requirements.get("max_beach_distance_m", 10**9):
        failed.append("BEACH_TOO_FAR")
    return failed
