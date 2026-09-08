from copy import deepcopy

import pytest

from hotel_recommendation.filters import evaluate_hard_filters
from hotel_recommendation.normalization import normalize_candidate
from hotel_recommendation.scoring import rank_key, score_candidate
from hotel_recommendation.selection import select_top_three


BASE_SIGNALS = {
    "budget": 100,
    "beach_location": 100,
    "service": 100,
    "reviews": 100,
    "traveler_fit": 100,
    "preferences": 100,
}


def candidate(hotel_id="1", price=100, review_count=10, signals=None):
    return {
        "hotel_id": hotel_id,
        "hotel_name": f"Hotel {hotel_id}",
        "price_total": price,
        "currency": "RUB",
        "beach_distance_m": 100,
        "family_friendly": True,
        "breakfast": True,
        "tripadvisor_rating": 4.8,
        "tripadvisor_review_count": review_count,
        "signals": deepcopy(signals or BASE_SIGNALS),
    }


def test_normalization_requires_exact_scoring_signals():
    raw = candidate()
    raw["signals"].pop("budget")
    with pytest.raises(ValueError, match="invalid signals"):
        normalize_candidate(raw)


def test_hard_filters_exclude_instead_of_penalize():
    request = {
        "currency": "RUB",
        "budget_total": 90,
        "mandatory_requirements": {
            "family": True,
            "breakfast": True,
            "max_beach_distance_m": 50,
        },
    }
    hotel = candidate()
    hotel["family_friendly"] = False
    hotel["breakfast"] = False
    assert evaluate_hard_filters(request, hotel) == [
        "OVER_BUDGET",
        "FAMILY_NOT_SUPPORTED",
        "REQUIRED_BREAKFAST_MISSING",
        "BEACH_TOO_FAR",
    ]


def test_score_breakdown_sums_to_total():
    scored = score_candidate(candidate(signals={
        "budget": 93,
        "beach_location": 90,
        "service": 93,
        "reviews": 87,
        "traveler_fit": 90,
        "preferences": 100,
    }))
    assert scored["score_breakdown"] == {
        "budget": 28,
        "beach_location": 18,
        "service": 14,
        "reviews": 13,
        "traveler_fit": 9,
        "preferences": 10,
    }
    assert scored["total_score"] == 92


def test_selection_roles_are_strategy_not_top_three_rank_positions():
    best = score_candidate(candidate("best", 225))
    value_signals = {**BASE_SIGNALS, "service": 80}
    value = score_candidate(candidate("value", 150, signals=value_signals))
    alternative_signals = {**BASE_SIGNALS, "budget": 90}
    alternative = score_candidate(candidate("alternative", 210, signals=alternative_signals))

    selected = select_top_three([alternative, value, best])
    assert [role for role, _ in selected] == ["best_match", "best_value", "alternative"]
    assert selected[1][1]["hotel_id"] == "value"


def test_rank_key_falls_back_to_hotel_id_after_equal_score_and_reviews():
    a = score_candidate(candidate("a", review_count=10))
    b = score_candidate(candidate("b", review_count=10))
    assert sorted([b, a], key=rank_key)[0]["hotel_id"] == "a"
