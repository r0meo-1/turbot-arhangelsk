from __future__ import annotations

import json
from pathlib import Path

from hotel_recommendation.feature_extraction import (
    FeatureRequest,
    HotelFeatureFacts,
    extract_signals,
)

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "hotel_recommendation" / "m2c5"
SCHEMA_VERSION = "m2c5-v1"


def _load(name: str) -> dict:
    return json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))


def _request(raw: dict) -> FeatureRequest:
    return FeatureRequest(
        budget_total=raw["budget_total"],
        currency=raw["currency"],
        adults=raw["adults"],
        children_ages=tuple(raw.get("children_ages", ())),
        preferences=tuple(raw.get("preferences", ())),
        max_beach_distance_m=raw.get("max_beach_distance_m"),
    )


def _facts(raw: dict) -> HotelFeatureFacts:
    return HotelFeatureFacts(
        canonical_hotel_id=raw["canonical_hotel_id"],
        total_price=raw["total_price"],
        currency=raw["currency"],
        beach_distance_m=raw["beach_distance_m"],
        tripadvisor_rating=raw["tripadvisor_rating"],
        tripadvisor_review_count=raw["tripadvisor_review_count"],
        service_rating=raw["service_rating"],
        family_friendly=raw["family_friendly"],
        breakfast=raw["breakfast"],
        amenities=frozenset(raw.get("amenities", ())),
    )


def test_m2c5_fixture_set_is_versioned_and_complete():
    fixtures = {
        "happy_path.json": "happy_path",
        "partial_pricing.json": "partial_pricing",
        "provider_timeout.json": "provider_timeout",
        "identity_mismatch.json": "identity_mismatch",
    }

    for filename, scenario in fixtures.items():
        payload = _load(filename)
        assert payload["schema_version"] == SCHEMA_VERSION
        assert payload["scenario"] == scenario


def test_happy_path_signals_match_feature_rules_exactly():
    payload = _load("happy_path.json")
    request = _request(payload["request"])

    actual = {}
    for raw in payload["candidates"]:
        assert "signals" not in raw
        result = extract_signals(request, _facts(raw))
        actual[result.canonical_hotel_id] = dict(result.signals)

    assert list(actual) == payload["expected"]["eligible_ids"]
    assert actual == payload["expected"]["signals"]
    assert payload["expected"]["pipeline_level"] == "healthy"


def test_partial_pricing_never_invents_missing_prices():
    payload = _load("partial_pricing.json")
    priced = []
    missing = []

    for candidate in payload["candidates"]:
        assert "signals" not in candidate
        if candidate["total_price"] is None:
            missing.append(candidate["canonical_hotel_id"])
            assert candidate["currency"] is None
        else:
            priced.append(candidate["canonical_hotel_id"])

    assert priced == payload["expected"]["eligible_ids"]
    assert set(missing) == set(payload["expected"]["excluded"])
    for hotel_id in missing:
        assert payload["expected"]["excluded"][hotel_id] == ["price_missing"]
    assert payload["expected"]["pipeline_level"] == "partial"


def test_provider_timeout_is_pipeline_level_degradation():
    payload = _load("provider_timeout.json")
    expected = payload["expected"]
    degradation = expected["provider_degradation"]

    assert payload["pricing_provider_outcome"]["status"] == "timeout"
    assert expected["eligible_ids"] == []
    assert degradation["code"] == "price_provider_timeout"
    assert degradation["affected_candidates"] == len(payload["content_candidates"])
    # Provider outage must not masquerade as N independent hotel price misses.
    assert expected["candidate_exclusions"] == {}
    assert expected["pipeline_level"] == "blocked"


def test_identity_mismatch_cannot_join_or_receive_synthetic_price():
    payload = _load("identity_mismatch.json")
    registry = payload["identity_snapshot"]
    content = payload["content_ref"]
    pricing = payload["pricing_ref"]
    expected = payload["expected"]

    content_key = f"{content['provider']}:{content['provider_hotel_id']}"
    pricing_key = f"{pricing['provider']}:{pricing['provider_hotel_id']}"

    assert registry[content_key] == expected["content_canonical_hotel_id"]
    assert pricing_key not in registry
    assert expected["pricing_identity_status"] == "unmapped"
    assert expected["pricing_canonical_hotel_id"] is None
    assert expected["joined"] is False
    assert expected["degradation_codes"] == ["identity_unmapped"]
    assert expected["synthetic_price"] is False
