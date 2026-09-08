from __future__ import annotations

import requests
import pytest

from hotel_recommendation.providers import (
    TripadvisorConfigurationError,
    TripadvisorRateLimitError,
    TripadvisorTerraClient,
    TripadvisorTerraSettings,
    TripadvisorUnavailableError,
)


HOTEL = {
    "tripadvisor_id": 1634352,
    "names": [
        {"language": "en-US", "value": "Renaissance Phuket Resort & Spa", "primary": True},
        {"language": "ru-RU", "value": "Renaissance Phuket Resort & Spa", "primary": False},
    ],
    "addresses": [{"language": "ru-RU", "value": "Пхукет, Таиланд", "primary": True}],
    "coordinates": {"latitude": 8.167, "longitude": 98.297},
    "traveler_ratings": {"overall": {"rating": 4.8, "review_count": 5811}},
    "attributes": [
        {"name": "Family rooms"},
        {"name": "Breakfast available"},
    ],
    "photos": [{"images": {"large": {"url": "https://images.example/hotel.jpg"}}}],
    "urls": {"tripadvisor": "https://www.tripadvisor.com/Hotel_Review-1634352"},
}


class FakeResponse:
    def __init__(self, status_code=200, payload=None, headers=None):
        self.status_code = status_code
        self._payload = payload
        self.headers = headers or {}

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


class FakeSession:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if not self.responses:
            raise AssertionError("unexpected network call")
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def settings(**overrides):
    values = {
        "api_key": "test-key",
        "base_url": "https://terra.tripadvisor.com/api",
        "timeout_seconds": 7.0,
        "cache_ttl_seconds": 60,
        "cache_max_entries": 8,
    }
    values.update(overrides)
    return TripadvisorTerraSettings(**values)


def test_search_hotels_maps_terra_facts_without_inventing_scoring_fields():
    session = FakeSession(
        FakeResponse(
            payload={"data": [HOTEL]},
            headers={"x-request-id": "ta-request-1"},
        )
    )
    client = TripadvisorTerraClient(settings(), session=session)

    result = client.search_hotels("Phuket", limit=10, locale="ru-RU")

    assert result.provider == "tripadvisor"
    assert result.destination == "Phuket"
    assert result.provider_request_id == "ta-request-1"
    assert result.stale_cache is False
    assert len(result.hotels) == 1

    hotel = result.hotels[0]
    assert hotel.hotel_id == "1634352"
    assert hotel.hotel_name == "Renaissance Phuket Resort & Spa"
    assert hotel.tripadvisor_rating == 4.8
    assert hotel.tripadvisor_review_count == 5811
    assert hotel.latitude == 8.167
    assert hotel.longitude == 98.297
    assert hotel.address == "Пхукет, Таиланд"
    assert hotel.photo_url == "https://images.example/hotel.jpg"
    assert "Family rooms" in hotel.attributes

    # Provider facts must not silently become recommendation inputs.
    assert not hasattr(hotel, "price_total")
    assert not hasattr(hotel, "signals")
    assert not hasattr(hotel, "beach_distance_m")
    assert not hasattr(hotel, "family_friendly")
    assert not hasattr(hotel, "breakfast")

    url, kwargs = session.calls[0]
    assert url == "https://terra.tripadvisor.com/api/locations/search"
    assert kwargs["params"] == {
        "query": "Phuket",
        "category": "HOTEL",
        "locale": "ru-RU",
        "size": 10,
    }
    assert kwargs["headers"]["X-API-Key"] == "test-key"
    assert kwargs["timeout"] == 7.0


def test_rate_limit_uses_stale_cache_when_available():
    now = [1000.0]
    session = FakeSession(
        FakeResponse(payload={"data": [HOTEL]}, headers={"x-request-id": "cached-request"}),
        FakeResponse(status_code=429, payload={"detail": "quota"}),
    )
    client = TripadvisorTerraClient(
        settings(cache_ttl_seconds=10),
        session=session,
        clock=lambda: now[0],
    )

    first = client.search_hotels("Phuket")
    assert first.stale_cache is False

    now[0] += 11
    second = client.search_hotels("Phuket")

    assert second.stale_cache is True
    assert second.provider_request_id == "cached-request"
    assert second.hotels == first.hotels
    assert len(session.calls) == 2


def test_rate_limit_without_cache_is_explicit():
    client = TripadvisorTerraClient(
        settings(),
        session=FakeSession(FakeResponse(status_code=429, payload={})),
    )
    with pytest.raises(TripadvisorRateLimitError):
        client.search_hotels("Phuket")


def test_network_failure_without_cache_is_explicit():
    client = TripadvisorTerraClient(
        settings(),
        session=FakeSession(requests.ConnectionError("offline")),
    )
    with pytest.raises(TripadvisorUnavailableError):
        client.search_hotels("Phuket")


def test_missing_api_key_fails_before_network():
    session = FakeSession()
    client = TripadvisorTerraClient(settings(api_key=""), session=session)

    with pytest.raises(TripadvisorConfigurationError):
        client.search_hotels("Phuket")

    assert session.calls == []


def test_location_details_and_photos_have_dedicated_endpoints():
    session = FakeSession(
        FakeResponse(payload=HOTEL),
        FakeResponse(
            payload={
                "data": [
                    {"images": {"large": {"url": "https://images.example/1.jpg"}}},
                    {"url": "https://images.example/2.jpg"},
                ]
            }
        ),
    )
    client = TripadvisorTerraClient(settings(), session=session)

    hotel = client.get_location("1634352")
    photos = client.get_photos("1634352", limit=2)

    assert hotel.hotel_id == "1634352"
    assert photos == (
        "https://images.example/1.jpg",
        "https://images.example/2.jpg",
    )
    assert session.calls[0][0].endswith("/locations/1634352")
    assert session.calls[1][0].endswith("/locations/1634352/photos")
