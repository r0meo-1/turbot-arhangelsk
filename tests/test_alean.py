import requests

import pytest

from shared import alean
from shared import tour_providers


class FakeResponse:
    def __init__(self, xml: str, status_code: int = 200):
        self.content = xml.encode("utf-8")
        self.status_code = status_code
        self.closed = False

    def iter_content(self, chunk_size=65536):
        for index in range(0, len(self.content), chunk_size):
            yield self.content[index:index + chunk_size]

    def close(self):
        self.closed = True


class FakeAleanSession:
    def __init__(self, fail_action: str = "", fail_status: int = 503):
        self.calls = []
        self.fail_action = fail_action
        self.fail_status = fail_status

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        action = (kwargs.get("params") or {}).get("action")
        if action == self.fail_action:
            return FakeResponse("<error />", self.fail_status)
        payloads = {
            "GetCountries": """
                <getCountriesResult version="2.2">
                  <country id="92" name="Таиланд" cid="country-th" />
                </getCountriesResult>
            """,
            "GetDepartCities": """
                <getDepartCitiesResult version="2.2">
                  <city id="17" name="Архангельск" cid="depart-arh">
                    <countryTo id="92" cid="country-th" />
                  </city>
                </getDepartCitiesResult>
            """,
            "GetResorts": """
                <getResortsResult version="2.2">
                  <resort id="7" name="Пхукет" countryId="92" cid="resort-phuket" />
                </getResortsResult>
            """,
            "GetHotelCategories": """
                <getHotelCategoriesResult version="2.2">
                  <hotelCategory id="5" name="5*" cid="cat-5" />
                </getHotelCategoriesResult>
            """,
            "GetHotels": """
                <getHotelsResult version="2.2">
                  <hotel id="100" name="Alean Test Resort" hotelCategoryId="5"
                         resortId="7" shortName="TEST" cid="hotel-test" />
                </getHotelsResult>
            """,
            "GetTours": """
                <getToursResult version="2.2" searchToken="safe-test-token"
                                departureTownShortName="ARH" departCityId="17">
                  <tour offerId="777" tourName="Thailand" tourCID="tour-th"
                        CheckInDate="2026-10-16T14:00:00"
                        CheckOutDate="2026-10-26T12:00:00"
                        hotelId="100" hotelCID="hotel-test" resortId="7"
                        hotelCategoryId="5" mealId="3" mealCode="AI"
                        htPlaceName="DBL" roomTypeName="Deluxe"
                        tourDate="16.10.2026" nights="10"
                        price="219000" hotelPrice="150000"
                        hotelIsInStop="0" ticketsIncluded="1"
                        hasEconomTicketsDpt="1" hasEconomTicketsRtn="1"
                        hasBusinessTicketsDpt="0" hasBusinessTicketsRtn="0"
                        finder="finder-1" line="line-1" tline="tline-1"
                        trline="" />
                </getToursResult>
            """,
        }
        return FakeResponse(payloads[action])


def _settings():
    return alean.AleanSettings(
        enabled=True,
        username="agency",
        password="private-value",
        max_offers=9,
        dictionary_cache_ttl=3600,
    )


def _info():
    return {
        "destination": "Таиланд",
        "origin": "Архангельск",
        "dates": "16-17 октября 2026",
        "nights": 10,
        "dates_are_trip": False,
        "people": "2",
        "kids_ages": [],
        "budget": 250000,
        "budget_scope": "total",
        "budget_open_ended": False,
    }


def test_alean_readiness_probe_is_read_only_and_uses_basic_auth():
    session = FakeAleanSession()
    result = alean.readiness_probe(_settings(), session)

    assert result == {"ok": True, "countries": 1, "depart_cities": 1}
    assert len(session.calls) == 2
    for url, kwargs in session.calls:
        assert url == "https://sapi.alean.ru:3443/services/xml/"
        assert kwargs["auth"] == ("agency", "private-value")
        assert kwargs["allow_redirects"] is False
        assert kwargs["stream"] is True
        assert "agency" not in str(kwargs["params"])
        assert "private-value" not in str(kwargs["params"])


def test_alean_search_maps_sapi_xml_to_turbot_offer_without_inventing_availability():
    alean.clear_catalog_cache()
    session = FakeAleanSession()

    result = alean.search_tours(_settings(), session, _info())

    assert result.error == ""
    assert len(result.offers) == 1
    offer = result.offers[0]
    assert offer.hotel == "Alean Test Resort"
    assert offer.category == 5
    assert offer.region == "Пхукет"
    assert offer.date == "16.10.2026"
    assert offer.nights == 10
    assert offer.meal == "AI"
    assert offer.room == "Deluxe"
    assert offer.operator == "Alean"
    assert offer.price == 219000
    assert offer.currency == "RUB"
    assert offer.departure == "Архангельск"
    assert offer.tour_id.startswith("alean:")

    search_call = next(
        call for call in session.calls
        if (call[1].get("params") or {}).get("action") == "GetTours"
    )
    params = search_call[1]["params"]
    assert params["countryCID"] == "country-th"
    assert params["departCityCID"] == "depart-arh"
    assert params["dateFrom"] == "16.10.2026"
    assert params["dateTo"] == "17.10.2026"
    assert params["nightsMin"] == 10
    assert params["nightsMax"] == 10
    assert params["currencyCode"] == "RUB"
    assert params["ticketsIncluded"] == 1
    assert params["hasTickets"] == 1
    assert params["excludeHotelsByRequest"] == 1
    assert params["priceMax"] == 250000


def test_alean_drops_unavailable_offer_even_if_provider_returns_it():
    alean.clear_catalog_cache()
    session = FakeAleanSession()
    original_get = session.get

    def get(url, **kwargs):
        response = original_get(url, **kwargs)
        if (kwargs.get("params") or {}).get("action") == "GetTours":
            response.content = response.content.replace(
                b'hotelIsInStop="0"', b'hotelIsInStop="1"'
            )
        return response

    session.get = get
    result = alean.search_tours(_settings(), session, _info())
    assert result.offers == []
    assert "не найдено" in result.error.lower()


def test_alean_direct_only_fails_closed_without_provider_call():
    session = FakeAleanSession()
    info = _info()
    info["direct_only"] = True

    result = alean.search_tours(_settings(), session, info)

    assert result.offers == []
    assert "прямого" in result.error.lower()
    assert session.calls == []


def test_alean_http_failure_returns_safe_error_without_response_body():
    alean.clear_catalog_cache()
    session = FakeAleanSession(fail_action="GetCountries", fail_status=401)

    result = alean.search_tours(_settings(), session, _info())

    assert result.offers == []
    assert result.error == "Alean SAPI HTTP 401"


def test_router_can_use_alean_and_marks_provider_identity():
    alean.clear_catalog_cache()
    settings = tour_providers.ProviderSettings(
        order=("alean",),
        alean=_settings(),
    )
    outcomes = []

    result, provider = tour_providers.search_tours(
        settings,
        FakeAleanSession(),
        _info(),
        on_outcome=lambda name, outcome: outcomes.append((name, outcome)),
    )

    assert provider == "alean"
    assert result.offers
    assert result.offers[0].provider == "alean"
    assert outcomes == [("alean", "success")]


def test_alean_is_not_enabled_merely_because_credentials_exist():
    settings = tour_providers.ProviderSettings(
        order=("alean",),
        alean=alean.AleanSettings(
            enabled=False,
            username="agency",
            password="private-value",
        ),
    )
    assert settings.enabled_names() == []


def test_alean_readiness_reports_http_status_without_leaking_payload():
    session = FakeAleanSession(fail_action="GetCountries", fail_status=503)
    result = alean.readiness_probe(_settings(), session)
    assert result == {"ok": False, "reason": "http_error", "status": 503}


def test_alean_rejects_hotel_only_result():
    alean.clear_catalog_cache()
    session = FakeAleanSession()
    original_get = session.get

    def get(url, **kwargs):
        response = original_get(url, **kwargs)
        if (kwargs.get("params") or {}).get("action") == "GetTours":
            response.content = response.content.replace(
                b'ticketsIncluded="1"', b'ticketsIncluded="0"'
            )
        return response

    session.get = get
    result = alean.search_tours(_settings(), session, _info())
    assert result.offers == []
    assert "не найдено" in result.error.lower()


def test_alean_respects_requested_hotel_filter():
    alean.clear_catalog_cache()
    session = FakeAleanSession()
    info = _info()
    info["hotel_query"] = "Different Hotel"

    result = alean.search_tours(_settings(), session, info)

    assert result.offers == []
    assert "не найдено" in result.error.lower()


def test_alean_declines_broad_departure_window_for_router_fallback():
    alean.clear_catalog_cache()
    session = FakeAleanSession()
    info = _info()
    info["dates"] = "следующий месяц"

    result = alean.search_tours(_settings(), session, info)

    assert result.offers == []
    assert "широкий диапазон" in result.error.lower()
    assert not any(
        (kwargs.get("params") or {}).get("action") == "GetTours"
        for _, kwargs in session.calls
    )


def test_alean_streaming_cap_stops_oversized_response():
    settings = _settings()
    settings.max_response_bytes = 1024
    response = FakeResponse("<root>" + ("x" * 5000) + "</root>")

    with pytest.raises(ValueError, match="size limit"):
        alean._bounded_xml(settings, response)

    assert response.closed is True
