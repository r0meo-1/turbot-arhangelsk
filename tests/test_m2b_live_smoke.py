from __future__ import annotations

from dataclasses import replace

import pytest

from hotel_recommendation.m2b_live_smoke import (
    LiveSmokeValidationError,
    validate_live_search_result,
)
from hotel_recommendation.providers import HotelFacts, HotelFactsSearchResult


HOTEL = HotelFacts(
    hotel_id="1634352",
    hotel_name="Renaissance Phuket Resort & Spa",
    tripadvisor_rating=4.8,
    tripadvisor_review_count=5811,
    latitude=8.167,
    longitude=98.297,
)


def result(**overrides):
    values = {
        "provider": "tripadvisor",
        "destination": "Phuket",
        "fetched_at": "2026-09-09T00:00:00+00:00",
        "provider_request_id": "terra-request-123",
        "hotels": (HOTEL,),
        "stale_cache": False,
    }
    values.update(overrides)
    return HotelFactsSearchResult(**values)


def test_live_smoke_accepts_fresh_non_empty_provider_facts():
    report = validate_live_search_result(result())

    assert report.destination == "Phuket"
    assert report.provider_request_id == "terra-request-123"
    assert report.hotel_count == 1
    assert report.stale_cache is False


def test_live_smoke_rejects_stale_cache():
    with pytest.raises(LiveSmokeValidationError, match="stale cache"):
        validate_live_search_result(result(stale_cache=True))


def test_live_smoke_requires_provider_trace_and_hotels():
    with pytest.raises(LiveSmokeValidationError, match="provider_request_id"):
        validate_live_search_result(result(provider_request_id=""))

    with pytest.raises(LiveSmokeValidationError, match="empty hotel list"):
        validate_live_search_result(result(hotels=()))


def test_live_smoke_requires_rating_and_valid_review_count():
    with pytest.raises(LiveSmokeValidationError, match="rating is missing"):
        validate_live_search_result(result(hotels=(replace(HOTEL, tripadvisor_rating=None),)))

    with pytest.raises(LiveSmokeValidationError, match="review_count is negative"):
        validate_live_search_result(result(hotels=(replace(HOTEL, tripadvisor_review_count=-1),)))


def test_provider_fact_shape_has_no_price_or_signals():
    report = validate_live_search_result(result())
    assert report.hotel_count == 1
    assert not hasattr(HOTEL, "price_total")
    assert not hasattr(HOTEL, "signals")
