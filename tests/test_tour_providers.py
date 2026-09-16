from __future__ import annotations

from typing import Any

from shared import tour_providers
from shared import tourvisor
from shared import travelata


class FakeResponse:
    def __init__(self, payload: Any, status_code: int = 200):
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            import requests

            response = requests.Response()
            response.status_code = self.status_code
            raise requests.HTTPError(response=response)

    def json(self):
        return self._payload


class FakeSession:
    def __init__(self):
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if url.endswith("/partners/directory/departureCities"):
            return FakeResponse({
                "success": True,
                "result": [{"id": 17, "name": "Архангельск", "disabled": False}],
            })
        if url.endswith("/partners/directory/countries"):
            return FakeResponse({
                "success": True,
                "result": [{"id": 92, "name": "Таиланд", "disabled": False}],
            })
        if url.endswith("/partners/statistic/cheapestTours"):
            return FakeResponse({
                "success": True,
                "result": [
                    {
                        "tourIdentity": "abc123",
                        "price": 198000,
                        "checkinDate": "2026-10-16",
                        "nights": 10,
                        "hotelId": 501,
                        "hotelName": "Real Beach Resort",
                        "hotelCategoryName": "5*",
                        "hotelRating": "4.7",
                        "hotelPreview": "https://img.example/hotel.jpg",
                        "mealId": 1,
                        "resortId": 2161,
                    }
                ],
            })
        if url.endswith("/partners/directory/resorts"):
            return FakeResponse({
                "success": True,
                "result": [{"id": 2161, "name": "Паттайя"}],
            })
        if url.endswith("/partners/directory/meals"):
            return FakeResponse({
                "success": True,
                "result": [{"id": 1, "code": "AI", "name": "Всё включено"}],
            })
        raise AssertionError(f"unexpected URL: {url}")


def _info():
    return {
        "destination": "Таиланд",
        "origin": "Архангельск",
        "dates": "16.10.2026",
        "nights": 10,
        "dates_are_trip": False,
        "people": "2",
        "kids_ages": [],
        "budget": 250000,
        "budget_scope": "total",
        "budget_open_ended": False,
    }


def test_travelata_search_maps_current_partner_api_to_turbot_offer():
    session = FakeSession()
    settings = travelata.TravelataSettings(
        enabled=True,
        username="partner",
        password="secret",
        max_offers=9,
    )

    result = travelata.search_tours(settings, session, _info())

    assert result.error == ""
    assert len(result.offers) == 1
    offer = result.offers[0]
    assert offer.hotel == "Real Beach Resort"
    assert offer.category == 5
    assert offer.region == "Паттайя"
    assert offer.date == "2026-10-16"
    assert offer.nights == 10
    assert offer.meal == "Всё включено"
    assert offer.price == 198000
    assert offer.fuel_charge == 0
    assert offer.departure == "Архангельск"
    assert offer.tour_id.startswith("travelata:")

    search_call = next(call for call in session.calls if call[0].endswith("cheapestTours"))
    params = search_call[1]["params"]
    assert params["countries[]"] == 92
    assert params["departureCity"] == 17
    assert params["nightRange[from]"] == 10
    assert params["nightRange[to]"] == 10
    assert search_call[1]["auth"] == ("partner", "secret")


def test_travelata_budget_filter_marks_real_search_for_over_budget_retry():
    session = FakeSession()
    settings = travelata.TravelataSettings(
        enabled=True, username="partner", password="secret"
    )
    info = _info()
    info["budget"] = 150000

    result = travelata.search_tours(settings, session, info)

    assert result.offers == []
    assert result.search_id == -1
    assert "не найдено" in result.error.lower()


def test_router_falls_through_from_travelata_to_tourvisor(monkeypatch):
    monkeypatch.setattr(
        tour_providers._travelata,
        "search_tours",
        lambda *args, **kwargs: tourvisor.SearchResult(error="Travelata unavailable"),
    )
    expected = tourvisor.TourOffer(
        hotel="Fallback Hotel",
        category=4,
        region="Анталья",
        date="2026-10-16",
        nights=10,
        meal="AI",
        room="",
        operator="",
        price=210000,
    )
    monkeypatch.setattr(
        tour_providers._tourvisor,
        "search_tours",
        lambda *args, **kwargs: tourvisor.SearchResult(offers=[expected], search_id=7),
    )

    settings = tour_providers.ProviderSettings(
        order=("travelata", "tourvisor"),
        travelata=travelata.TravelataSettings(
            enabled=True, username="u", password="p"
        ),
        tourvisor=tourvisor.TourvisorSettings(enabled=True, token="token"),
    )
    result, provider = tour_providers.search_tours(
        settings, FakeSession(), _info()
    )

    assert provider == "tourvisor"
    assert result.offers == [expected]


def test_router_has_no_fake_offer_when_no_provider_is_configured():
    result, provider = tour_providers.search_tours(
        tour_providers.ProviderSettings(), FakeSession(), _info()
    )
    assert provider == ""
    assert result.offers == []
    assert "не настроен" in result.error.lower()
