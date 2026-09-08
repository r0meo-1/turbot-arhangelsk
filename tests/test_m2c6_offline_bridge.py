from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from hotel_recommendation.bridge import (
    BridgeRequest,
    OfflineCandidateFacts,
    run_offline_bridge,
)
from hotel_recommendation.degradation import DegradationCode, PipelineDegradationLevel
from hotel_recommendation.feature_extraction import FeatureRequest
from hotel_recommendation.identity import CanonicalIdentityResolver, ProviderHotelRef

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "hotel_recommendation" / "m2c5"
CHECK_IN = date(2026, 10, 9)
CHECK_OUT = date(2026, 10, 19)


def _load(name: str) -> dict:
    return json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))


def _feature_request(raw: dict) -> FeatureRequest:
    return FeatureRequest(
        budget_total=raw["budget_total"],
        currency=raw["currency"],
        adults=raw["adults"],
        children_ages=tuple(raw.get("children_ages", ())),
        preferences=tuple(raw.get("preferences", ())),
        max_beach_distance_m=raw.get("max_beach_distance_m"),
    )


def _bridge_request(raw: dict) -> BridgeRequest:
    return BridgeRequest(
        feature_request=_feature_request(raw),
        check_in=CHECK_IN,
        check_out=CHECK_OUT,
    )


def _refs(canonical_id: str) -> tuple[ProviderHotelRef, ProviderHotelRef]:
    return (
        ProviderHotelRef("tripadvisor_terra", f"ta-{canonical_id}"),
        ProviderHotelRef("commercial_price_fixture", f"price-{canonical_id}"),
    )


def _resolver_for(raw_candidates: list[dict], *, include_missing_prices: bool = True) -> CanonicalIdentityResolver:
    forward = {}
    for raw in raw_candidates:
        canonical_id = raw["canonical_hotel_id"]
        content_ref, pricing_ref = _refs(canonical_id)
        forward[content_ref] = canonical_id
        if include_missing_prices or raw.get("total_price") is not None:
            forward[pricing_ref] = canonical_id
    return CanonicalIdentityResolver(forward)


def _candidate(raw: dict) -> OfflineCandidateFacts:
    content_ref, pricing_ref = _refs(raw["canonical_hotel_id"])
    return OfflineCandidateFacts(
        content_ref=content_ref,
        pricing_ref=pricing_ref if raw.get("total_price") is not None else None,
        name=raw["canonical_hotel_id"],
        total_price=raw.get("total_price"),
        currency=raw.get("currency"),
        price_check_in=CHECK_IN,
        price_check_out=CHECK_OUT,
        price_adults=2,
        price_children=1,
        beach_distance_m=raw.get("beach_distance_m", 0),
        tripadvisor_rating=raw.get("tripadvisor_rating", 4.5),
        tripadvisor_review_count=raw.get("tripadvisor_review_count", 100),
        service_rating=raw.get("service_rating", 4.5),
        family_friendly=raw.get("family_friendly", True),
        breakfast=raw.get("breakfast", True),
        amenities=frozenset(raw.get("amenities", ())),
    )


def test_m2c6_happy_path_is_deterministic_from_identity_to_top_three():
    payload = _load("happy_path.json")
    candidates = [_candidate(raw) for raw in payload["candidates"]]
    resolver = _resolver_for(payload["candidates"])
    request = _bridge_request(payload["request"])

    first = run_offline_bridge(request=request, candidates=candidates, identity_resolver=resolver)
    second = run_offline_bridge(request=request, candidates=candidates, identity_resolver=resolver)

    assert first == second
    assert first.level == PipelineDegradationLevel.HEALTHY
    assert first.eligible_ids == tuple(payload["expected"]["eligible_ids"])
    assert first.exclusions == ()

    actual_signals = {hotel["hotel_id"]: hotel["signals"] for hotel in first.scored}
    assert actual_signals == payload["expected"]["signals"]

    assert [(role, hotel["hotel_id"], hotel["total_score"]) for role, hotel in first.top_three] == [
        ("best_match", "hotel_phuket_001", 94),
        ("best_value", "hotel_phuket_002", 94),
        ("alternative", "hotel_phuket_003", 88),
    ]


def test_m2c6_partial_pricing_returns_partial_without_synthetic_cards():
    payload = _load("partial_pricing.json")
    candidates = [_candidate(raw) for raw in payload["candidates"]]
    resolver = _resolver_for(payload["candidates"], include_missing_prices=False)

    result = run_offline_bridge(
        request=_bridge_request(payload["request"]),
        candidates=candidates,
        identity_resolver=resolver,
    )

    assert result.level == PipelineDegradationLevel.PARTIAL
    assert result.eligible_ids == tuple(payload["expected"]["eligible_ids"])
    assert len(result.top_three) == 2
    assert {item.canonical_hotel_id: [code.value for code in item.codes] for item in result.exclusions} == payload["expected"]["excluded"]
    assert all(hotel["price_total"] is not None for _, hotel in result.top_three)


def test_m2c6_provider_timeout_is_one_pipeline_incident():
    payload = _load("provider_timeout.json")
    raw_candidates = [
        {
            "canonical_hotel_id": item["canonical_hotel_id"],
            "total_price": None,
            "currency": None,
        }
        for item in payload["content_candidates"]
    ]
    candidates = [_candidate(raw) for raw in raw_candidates]
    resolver = _resolver_for(raw_candidates, include_missing_prices=False)

    result = run_offline_bridge(
        request=_bridge_request(payload["request"]),
        candidates=candidates,
        identity_resolver=resolver,
        price_provider_outage=DegradationCode.PRICE_PROVIDER_TIMEOUT,
        price_provider_name=payload["pricing_provider_outcome"]["provider"],
    )

    expected = payload["expected"]
    assert result.level.value == expected["pipeline_level"]
    assert result.eligible_ids == ()
    assert result.exclusions == ()
    assert result.top_three == ()
    assert result.provider_degradation is not None
    assert result.provider_degradation.provider == expected["provider_degradation"]["provider"]
    assert result.provider_degradation.code.value == expected["provider_degradation"]["code"]
    assert result.provider_degradation.affected_candidates == expected["provider_degradation"]["affected_candidates"]


def test_m2c6_identity_mismatch_blocks_price_join_without_price_missing():
    payload = _load("identity_mismatch.json")
    content_raw = payload["content_ref"]
    pricing_raw = payload["pricing_ref"]
    content_ref = ProviderHotelRef(content_raw["provider"], content_raw["provider_hotel_id"])
    pricing_ref = ProviderHotelRef(pricing_raw["provider"], pricing_raw["provider_hotel_id"])

    forward = {content_ref: payload["expected"]["content_canonical_hotel_id"]}
    resolver = CanonicalIdentityResolver(forward)
    request = BridgeRequest(
        feature_request=FeatureRequest(
            budget_total=270000,
            currency="RUB",
            adults=2,
            children_ages=(5,),
            preferences=("beach", "pool", "breakfast", "family"),
            max_beach_distance_m=1500,
        ),
        check_in=CHECK_IN,
        check_out=CHECK_OUT,
    )
    candidate = OfflineCandidateFacts(
        content_ref=content_ref,
        pricing_ref=pricing_ref,
        name="Identity mismatch fixture hotel",
        total_price=200000,
        currency="RUB",
        price_check_in=CHECK_IN,
        price_check_out=CHECK_OUT,
        price_adults=2,
        price_children=1,
        beach_distance_m=200,
        tripadvisor_rating=4.7,
        tripadvisor_review_count=1000,
        service_rating=4.5,
        family_friendly=True,
        breakfast=True,
        amenities=frozenset({"pool"}),
    )

    result = run_offline_bridge(request=request, candidates=[candidate], identity_resolver=resolver)

    assert result.level == PipelineDegradationLevel.BLOCKED
    assert result.eligible_ids == ()
    assert result.scored == ()
    assert result.top_three == ()
    assert len(result.exclusions) == 1
    assert [code.value for code in result.exclusions[0].codes] == payload["expected"]["degradation_codes"]
    assert DegradationCode.PRICE_MISSING not in result.exclusions[0].codes
