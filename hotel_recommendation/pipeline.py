from __future__ import annotations

from copy import deepcopy

from .config import SCORING_VERSION, TIE_BREAK_DESCRIPTION
from .explanation import build_fallback_explanation
from .filters import evaluate_hard_filters
from .identity import deterministic_id, stable_hash
from .lead import build_qualified_lead
from .models import ProviderSnapshot, Recommendation, SearchRequest, VerticalSliceResult
from .normalization import normalize_candidate
from .scoring import score_candidate
from .selection import select_top_three


def run_vertical_slice(
    request: SearchRequest,
    snapshot: ProviderSnapshot,
    *,
    selected_hotel_id: str,
) -> VerticalSliceResult:
    request, snapshot = deepcopy(request), deepcopy(snapshot)
    snapshot_hash = stable_hash(snapshot)
    normalized = [normalize_candidate(hotel) for hotel in snapshot["candidates"]]

    eligible = []
    filtered_out = []
    for hotel in normalized:
        failed = evaluate_hard_filters(request, hotel)
        if failed:
            filtered_out.append({"hotel_id": hotel["hotel_id"], "failed_filters": failed})
        else:
            eligible.append(hotel)

    scored = [score_candidate(hotel) for hotel in eligible]
    recommendation_id = deterministic_id(
        "rec",
        request["request_id"],
        snapshot_hash,
        SCORING_VERSION,
    )

    recommendations: list[Recommendation] = []
    for role, hotel in select_top_three(scored):
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
            "explanation": build_fallback_explanation(request, hotel),
        })

    selected = next(
        (
            recommendation
            for recommendation in recommendations
            if recommendation["hotel_id"] == str(selected_hotel_id)
        ),
        None,
    )
    if selected is None:
        raise ValueError("selected_hotel_id must be present in TOP-3")

    lead = build_qualified_lead(
        request,
        snapshot,
        snapshot_hash,
        recommendation_id,
        selected,
    )

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
            "normalized_candidate_ids": [hotel["hotel_id"] for hotel in normalized],
            "filtered_out": filtered_out,
            "eligible_candidate_ids": [hotel["hotel_id"] for hotel in eligible],
            "tie_break": TIE_BREAK_DESCRIPTION,
        },
        "recommendations": recommendations,
        "selected_recommendation_id": recommendation_id,
        "selected_hotel_id": selected["hotel_id"],
        "qualified_lead": lead,
    }
