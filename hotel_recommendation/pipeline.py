from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from typing import Any

SCORING_VERSION = "hotel-v1.0"
WEIGHTS = {
    "budget": 30,
    "beach_location": 20,
    "service": 15,
    "reviews": 15,
    "traveler_fit": 10,
    "preferences": 10,
}
LABELS = {
    "budget": "бюджет",
    "beach_location": "пляж и локация",
    "service": "сервис",
    "reviews": "отзывы",
    "traveler_fit": "состав туристов",
    "preferences": "пожелания",
}


def _hash(value: Any) -> str:
    data = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(data.encode()).hexdigest()


def _id(prefix: str, *parts: str) -> str:
    return f"{prefix}_{hashlib.sha256('|'.join(parts).encode()).hexdigest()[:16]}"


def _normalize(raw: dict[str, Any]) -> dict[str, Any]:
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


def _filter(request: dict[str, Any], hotel: dict[str, Any]) -> list[str]:
    req = request.get("mandatory_requirements", {})
    failed = []
    if hotel["currency"] != request["currency"]:
        failed.append("CURRENCY_MISMATCH")
    if hotel["price_total"] > request["budget_total"]:
        failed.append("OVER_BUDGET")
    if req.get("family") and not hotel["family_friendly"]:
        failed.append("FAMILY_NOT_SUPPORTED")
    if req.get("breakfast") and not hotel["breakfast"]:
        failed.append("REQUIRED_BREAKFAST_MISSING")
    if hotel["beach_distance_m"] > req.get("max_beach_distance_m", 10**9):
        failed.append("BEACH_TOO_FAR")
    return failed


def _score(hotel: dict[str, Any]) -> dict[str, Any]:
    breakdown = {k: round(hotel["signals"][k] * w / 100) for k, w in WEIGHTS.items()}
    return {**hotel, "score_breakdown": breakdown, "total_score": sum(breakdown.values())}


def _rank(hotel: dict[str, Any]) -> tuple[Any, ...]:
    return (
        -hotel["total_score"],
        -hotel["score_breakdown"]["reviews"],
        -hotel["tripadvisor_review_count"],
        hotel["hotel_id"],
    )


def _explain(request: dict[str, Any], hotel: dict[str, Any]) -> dict[str, Any]:
    ratios = {k: hotel["score_breakdown"][k] / WEIGHTS[k] for k in WEIGHTS}
    ordered = sorted(ratios, key=lambda k: (-ratios[k], list(WEIGHTS).index(k)))
    strengths = [LABELS[k] for k in ordered[:3]]
    watch_out = [LABELS[k] for k in reversed(ordered) if ratios[k] < 0.75][:2]
    return {
        "best_for": "Семья с ребёнком" if request.get("children_ages") else "Взрослые туристы",
        "recommendation_reason": "Сильнее всего совпадает по: " + ", ".join(strengths) + ".",
        "strengths": strengths,
        "watch_out": watch_out,
        "mode": "fallback",
    }


def _select(scored: list[dict[str, Any]]) -> list[tuple[str, dict[str, Any]]]:
    ranked = sorted(scored, key=_rank)
    if not ranked:
        return []
    best = ranked[0]
    used = {best["hotel_id"]}
    result = [("best_match", best)]
    value_pool = [h for h in ranked if h["hotel_id"] not in used and h["total_score"] >= 80]
    if value_pool:
        value = min(value_pool, key=lambda h: (h["price_total"], *_rank(h)))
        result.append(("best_value", value))
        used.add(value["hotel_id"])
    alternatives = [h for h in ranked if h["hotel_id"] not in used]
    if alternatives:
        result.append(("alternative", sorted(alternatives, key=_rank)[0]))
    return result[:3]


def run_vertical_slice(request: dict[str, Any], snapshot: dict[str, Any], *, selected_hotel_id: str) -> dict[str, Any]:
    request, snapshot = deepcopy(request), deepcopy(snapshot)
    snapshot_hash = _hash(snapshot)
    normalized = [_normalize(h) for h in snapshot["candidates"]]
    eligible, filtered_out = [], []
    for hotel in normalized:
        failed = _filter(request, hotel)
        if failed:
            filtered_out.append({"hotel_id": hotel["hotel_id"], "failed_filters": failed})
        else:
            eligible.append(hotel)

    scored = [_score(h) for h in eligible]
    recommendation_id = _id("rec", request["request_id"], snapshot_hash, SCORING_VERSION)
    recommendations = []
    for role, hotel in _select(scored):
        recommendations.append({
            "recommendation_id": recommendation_id,
            "role": role,
            "hotel_id": hotel["hotel_id"],
            "hotel_name": hotel["hotel_name"],
            "match_score": hotel["total_score"],
            "score_breakdown": hotel["score_breakdown"],
            "price_total": hotel["price_total"],
            "currency": hotel["currency"],
            "tripadvisor_rating": hotel["tripadvisor_rating"],
            "tripadvisor_review_count": hotel["tripadvisor_review_count"],
            "explanation": _explain(request, hotel),
        })

    selected = next((r for r in recommendations if r["hotel_id"] == str(selected_hotel_id)), None)
    if selected is None:
        raise ValueError("selected_hotel_id must be present in TOP-3")

    lead = {
        "lead_id": _id("lead", recommendation_id, selected["hotel_id"]),
        "recommendation_id": recommendation_id,
        "source": request["source"],
        "hotel_id": selected["hotel_id"],
        "hotel_name": selected["hotel_name"],
        "match_score": selected["match_score"],
        "score_breakdown": selected["score_breakdown"],
        "tripadvisor_rating": selected["tripadvisor_rating"],
        "tripadvisor_review_count": selected["tripadvisor_review_count"],
        "selection_role": selected["role"],
        "reason_selected": request["reason_selected"],
        "scoring_version": SCORING_VERSION,
        "provider": snapshot["provider"],
        "provider_snapshot_at": snapshot["provider_snapshot_at"],
        "provider_response_hash": snapshot_hash,
        "search_request": {k: request[k] for k in ("request_id", "destination", "adults", "children_ages", "budget_total", "currency", "preferences")},
    }
    return {
        "request": request,
        "provider_trace": {
            "provider": snapshot["provider"],
            "provider_request_id": snapshot["provider_request_id"],
            "provider_snapshot_at": snapshot["provider_snapshot_at"],
            "provider_response_hash": snapshot_hash,
        },
        "scoring_version": SCORING_VERSION,
        "trace": {
            "normalized_candidate_ids": [h["hotel_id"] for h in normalized],
            "filtered_out": filtered_out,
            "eligible_candidate_ids": [h["hotel_id"] for h in eligible],
            "tie_break": ["total_score DESC", "reviews_score DESC", "tripadvisor_review_count DESC", "hotel_id ASC"],
        },
        "recommendations": recommendations,
        "selected_recommendation_id": recommendation_id,
        "selected_hotel_id": selected["hotel_id"],
        "qualified_lead": lead,
    }
