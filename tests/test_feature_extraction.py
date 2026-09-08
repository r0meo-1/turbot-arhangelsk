from __future__ import annotations

import pytest

from hotel_recommendation.feature_extraction import (
    FEATURE_RULES_VERSION,
    FeatureExtractionError,
    FeatureRequest,
    HotelFeatureFacts,
    extract_signals,
)


def _request(**overrides):
    values = {
        "budget_total": 270000,
        "currency": "RUB",
        "adults": 2,
        "children_ages": (5,),
        "preferences": ("beach", "pool", "breakfast", "family"),
        "max_beach_distance_m": 1500,
    }
    values.update(overrides)
    return FeatureRequest(**values)


def _facts(**overrides):
    values = {
        "canonical_hotel_id": "hotel_phuket_001",
        "total_price": 225000,
        "currency": "RUB",
        "beach_distance_m": 120,
        "tripadvisor_rating": 4.8,
        "tripadvisor_review_count": 5811,
        "service_rating": 4.6,
        "family_friendly": True,
        "breakfast": True,
        "amenities": frozenset({"pool", "wifi"}),
    }
    values.update(overrides)
    return HotelFeatureFacts(**values)


def test_extract_signals_is_exact_and_deterministic():
    first = extract_signals(_request(), _facts())
    second = extract_signals(_request(), _facts())

    assert first == second
    assert first.rules_version == FEATURE_RULES_VERSION
    assert first.signals == {
        "budget": 90,
        "beach_location": 95,
        "service": 90,
        "reviews": 96,
        "traveler_fit": 100,
        "preferences": 100,
    }


def test_budget_score_has_explicit_ceiling_behavior():
    at_ceiling = extract_signals(_request(), _facts(total_price=270000))
    over_budget = extract_signals(_request(), _facts(total_price=270001))

    assert at_ceiling.signals["budget"] == 70
    assert over_budget.signals["budget"] == 0


def test_preferences_use_exact_normalized_fact_membership():
    result = extract_signals(
        _request(preferences=("beach", "pool", "breakfast", "family", "spa")),
        _facts(),
    )

    assert result.signals["preferences"] == 80


def test_family_fit_only_applies_when_children_are_present():
    family_trip = extract_signals(_request(), _facts(family_friendly=False))
    adults_trip = extract_signals(
        _request(children_ages=()),
        _facts(family_friendly=False),
    )

    assert family_trip.signals["traveler_fit"] == 0
    assert adults_trip.signals["traveler_fit"] == 100


def test_currency_mismatch_is_not_silently_scored():
    with pytest.raises(FeatureExtractionError, match="currency mismatch"):
        extract_signals(_request(currency="RUB"), _facts(currency="USD"))


def test_service_requires_a_real_rating_instead_of_a_default():
    with pytest.raises(FeatureExtractionError, match="service_rating"):
        extract_signals(_request(), _facts(service_rating=0.0))
