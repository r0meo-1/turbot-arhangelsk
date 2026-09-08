from __future__ import annotations

from .config import SCORING_VERSION
from .identity import deterministic_id
from .models import ProviderSnapshot, QualifiedLead, Recommendation, SearchRequest


def build_qualified_lead(
    request: SearchRequest,
    snapshot: ProviderSnapshot,
    snapshot_hash: str,
    recommendation_id: str,
    selected: Recommendation,
) -> QualifiedLead:
    return {
        "lead_id": deterministic_id("lead", recommendation_id, selected["hotel_id"]),
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
        "search_request": {
            key: request[key]
            for key in (
                "request_id",
                "destination",
                "adults",
                "children_ages",
                "budget_total",
                "currency",
                "preferences",
            )
        },
    }
