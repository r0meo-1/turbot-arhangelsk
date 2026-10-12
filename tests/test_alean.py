from dataclasses import asdict
from datetime import date, timedelta
from concurrent.futures import ThreadPoolExecutor
from threading import Event
import xml.etree.ElementTree as ET

import pytest
import requests

from shared import alean, tour_providers as router, tourvisor as tv


@pytest.fixture(autouse=True)
def empty_cache():
    alean._cache.clear()
    yield
    alean._cache.clear()


def info(**updates):
    value = dict(destination="Россия", origin="Москва", dates=(date.today()+timedelta(days=30)).isoformat(),
                 dates_are_trip=False, nights="7", people=2, kids_ages=[0,15], budget=100000,
                 budget_scope="total", phone="private-phone", name="private-name")
    value.update(updates)
    return value


def settings(**updates):
    return alean.AleanSettings(enabled=True, username="fixture-user", password="fixture-secret", **updates)


class Response:
    def __init__(self, content, status=200):
        self.content = content.encode() if isinstance(content,str) else content
        self.status_code = status
    def __enter__(self):
        return self
    def __exit__(self,*args):
        pass
    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError("never log fixture-secret", response=self)
    def iter_content(self, size):
        yield self.content


class Session:
    def __init__(self, change=None, bad=None):
        self.calls, self.change, self.bad = [], change or {}, bad or {}
    def get(self, url, **kwargs):
        self.calls.append((url,kwargs))
        action = kwargs["params"]["action"]
        if action in self.bad:
            return self.bad[action]
        entries = {
            "GetCountries": '<country id="1" cid="RU-CID" name="Россия"/>',
            "GetDepartCities": '<city id="2" cid="MOW-CID" name="Москва"/>',
            "GetResorts": '<resort id="3" cid="SOCHI-CID" countryId="1" name="Сочи"/>',
            "GetHotels": '<hotel id="4" cid="HOTEL-CID" resortId="3" name="Тестовый отель"/>',
            "GetHotelCategories": '<hotelCategory id="5" cid="STAR-CID" name="4*"/>',
        }
        if action == "GetTours":
            params = kwargs["params"]
            row = dict(offerId="99", hotelId="4", hotelCID="HOTEL-CID", hotelCategoryId="5",
                       resortId="3", mealCode="HB", roomTypeName="Стандарт", price="80000",
                       tourDate=params["dateFrom"], nights="7", ticketsIncluded="1",
                       hotelIsInStop="0",hasEconomTicketsDpt="1",hasEconomTicketsRtn="1")
            row.update(self.change)
            root = ET.Element("getToursResult",searchToken="not-a-confirmation")
            ET.SubElement(root,"tour",row)
            return Response(ET.tostring(root))
        return Response(f'<{action[0].lower()+action[1:]}Result>{entries[action]}</{action[0].lower()+action[1:]}Result>')


def test_search_uses_documented_cids_dates_children_and_total_budget():
    session = Session()
    result = alean.search_tours(settings(), session, info())
    assert len(result.offers) == 1
    offer = result.offers[0]
    assert (offer.hotel, offer.category, offer.price, offer.provider) == ("Тестовый отель",4,80000,"alean")
    params = session.calls[-1][1]["params"]
    assert params["countryCID"] == "RU-CID"
    assert params["departCityCID"] == "MOW-CID"
    assert params["kidsAges"] == "0,15" and params["adults"] == 2 and params["kids"] == 2
    assert params["priceMax"] == 100000 and params["currencyCode"] == "RUB"
    assert params["ticketsIncluded"] == 1 and params["hasTickets"] == 1
    assert all("private-" not in repr(call) for call in session.calls)
    assert all(call[1]["allow_redirects"] is False for call in session.calls)
    assert all(call[1]["auth"] == ("fixture-user","fixture-secret") for call in session.calls)
    assert "fixture-secret" not in repr(settings())
    assert "трансфер не включён" in tv.format_client_message(result)


def test_resort_and_hotel_search_do_not_broaden_the_request():
    session = Session()
    assert alean.search_tours(settings(),session,info(destination="Сочи",hotel_query="Тестовый отель")).offers
    params = session.calls[-1][1]["params"]
    assert params["resortCIDs"] == "SOCHI-CID" and params["hotelCIDs"] == "HOTEL-CID"
    assert not alean.search_tours(settings(),session,info(destination="Соч")).offers


@pytest.mark.parametrize("updates", [dict(direct_only=True),dict(people=0),dict(kids_ages=[18]),
    dict(kids_ages=[True]),dict(nights="5-10"),dict(nights="50"),dict(nights=0),dict(nights="10-7"),
    dict(budget=-1),dict(budget=2147483648),dict(dates="Даты гибкие")])
def test_unsupported_or_invalid_request_never_calls_supplier(updates):
    session = Session()
    assert alean.search_tours(settings(),session,info(**updates)).error
    assert not session.calls


@pytest.mark.parametrize("change", [dict(price="0"),dict(price="100001"),dict(price="nan"),
    dict(hotelIsInStop="1"),dict(hotelIsInStop="2"),dict(ticketsIncluded="0"),
    dict(hasEconomTicketsDpt="2"),dict(hotelId="unknown"),dict(resortId="unknown"),
    dict(tourDate="01.01.2020"),dict(nights="20")])
def test_no_unsuitable_or_unverified_inventory(change):
    result = alean.search_tours(settings(),Session(change),info())
    assert not result.offers


def test_catalog_cache_is_per_account_and_search_is_always_fresh():
    session = Session()
    alean.search_tours(settings(),session,info())
    alean.search_tours(settings(),session,info())
    assert len(session.calls) == 7  # five catalogs, two live searches
    other = settings()
    other.password = "another-secret"
    alean.search_tours(other,session,info())
    assert len(session.calls) == 13


def test_catalog_cache_expires(monkeypatch):
    session = Session()
    monkeypatch.setattr(alean.time,"monotonic",lambda: 0)
    alean.search_tours(settings(),session,info())
    monkeypatch.setattr(alean.time,"monotonic",lambda: 86401)
    alean.search_tours(settings(),session,info())
    assert len(session.calls) == 12


def test_slow_catalog_request_does_not_block_another_catalog():
    entered, release = Event(), Event()
    class SlowCountries(Session):
        def get(self, url, **kwargs):
            if kwargs["params"]["action"] == "GetCountries":
                entered.set()
                assert release.wait(5)
            return super().get(url, **kwargs)
    session = SlowCountries()
    with ThreadPoolExecutor(max_workers=2) as workers:
        slow = workers.submit(alean._catalog, settings(), session, "GetCountries", account=b"fixture")
        try:
            assert entered.wait(2)
            fast = workers.submit(alean._catalog, settings(), session, "GetDepartCities", account=b"fixture")
            assert fast.result(timeout=2)[0]["name"] == "Москва"
        finally:
            release.set()
        assert slow.result(timeout=2)[0]["name"] == "Россия"


def test_completed_empty_search_allows_explicit_vk_budget_retry():
    config = router.ProviderSettings(order=("alean",), alean=settings())
    # The valid fixture offer is above the customer's cap; only a fresh,
    # explicitly requested open-ended search may return it.
    session = Session()
    capped, provider = router.search_tours(config, session, info(budget=50000))
    assert not capped.offers and capped.search_id and not provider
    assert session.calls[-1][1]["params"]["priceMax"] == 50000
    uncapped, provider = router.search_tours(config, session, info(budget=50000, budget_open_ended=True))
    assert provider == "alean" and uncapped.offers[0].price == 80000
    assert "priceMax" not in session.calls[-1][1]["params"]


def test_failed_search_does_not_allow_vk_budget_retry():
    config = router.ProviderSettings(order=("alean",), alean=settings())
    result, provider = router.search_tours(
        config, Session(bad={"GetTours": Response('', 401)}), info(),
    )
    assert result.error and not result.offers and result.search_id is None and not provider


@pytest.mark.parametrize("response", [Response('<error>fixture-secret</error>'),Response('not XML'),
    Response('<!DOCTYPE x [<!ENTITY a "test">]><getCountriesResult/>'),Response('',302),Response('',401)])
def test_bad_responses_fail_softly_without_logging_credentials(response,caplog):
    result = alean.search_tours(settings(),Session(bad={"GetCountries":response}),info())
    assert result.error and not result.offers
    assert "fixture-secret" not in result.error + caplog.text


def test_router_opt_in_and_actualization_remains_unconfirmed(monkeypatch):
    config = router.ProviderSettings(order=("alean",),alean=settings())
    assert config.enabled_names() == ["alean"]
    result, provider = router.search_tours(config,Session(),info())
    assert provider == "alean"
    actual = router.actualize_offer(config,Session(),asdict(result.offers[0]))
    assert actual["confirmed"] is False and actual["status"] == "unknown"
    assert "Specify" in actual["hotel_status"]
    config.order = ("tourvisor",)
    assert not config.enabled_names()
    monkeypatch.setenv("ALEAN_USERNAME","configured")
    monkeypatch.setenv("ALEAN_PASSWORD","secret")
    monkeypatch.delenv("ALEAN_ENABLED",raising=False)
    assert not alean.AleanSettings.from_env().enabled


def test_router_falls_back_after_alean_failure(monkeypatch):
    config = router.ProviderSettings(order=("alean","tourvisor"),alean=settings(),
                                    tourvisor=tv.TourvisorSettings(enabled=True,token="token"))
    expected = tv.SearchResult(offers=[tv.TourOffer("Fallback",0,"","",7,"","","",90000)])
    monkeypatch.setattr(tv,"search_tours",lambda *args,**kwargs:expected)
    result, provider = router.search_tours(config,Session(bad={"GetCountries":Response('',401)}),info())
    assert result is expected and provider == "tourvisor"


def test_vk_health_and_selected_offer_show_alean_without_secrets(monkeypatch):
    import vk_bot
    config = router.ProviderSettings(order=("alean",),alean=settings())
    monkeypatch.setattr(vk_bot,"_tour_provider_settings",lambda:config)
    health = vk_bot._tour_search_health()
    assert health["alean"] == {"configured":True,"enabled":True,"actualization":"manager_required"}
    assert "fixture-secret" not in repr(health)
    offer = alean.search_tours(settings(),Session(),info()).offers[0]
    summary = vk_bot._selected_tour_summary({"selected_tour":asdict(offer)})
    assert "Провайдер: Алеан" in summary and "трансфер не включён" in summary
