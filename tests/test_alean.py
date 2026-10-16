from dataclasses import asdict
from datetime import date, timedelta
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from threading import Lock
import xml.etree.ElementTree as ET
import json

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


class SochiDistricts(Session):
    def __init__(self, resort_id="6", extra=""):
        super().__init__(change={"resortId": resort_id, "price": "24830.72"})
        self.extra = extra

    def get(self, url, **kwargs):
        if kwargs["params"]["action"] == "GetResorts":
            self.calls.append((url, kwargs))
            return Response('<getResortsResult>'
                '<resort id="3" cid="SOCHI-CID" countryId="1" name="Сочи"/>'
                '<resort id="6" cid="ADLER-CID" countryId="1" name="Адлер"/>'
                '<resort id="7" cid="LAZ-CID" countryId="1" name="Лазаревское"/>'
                '<resort id="8" cid="MAM-CID" countryId="1" name="Мамайка"/>'
                '<resort id="9" cid="CENTER-CID" countryId="1" name="Сочи центр"/>'
                '<resort id="10" cid="OTHER-CID" countryId="1" name="Алушта"/>'
                '<resort id="11" cid="FOREIGN-CID" countryId="2" name="Мамайка"/>'
                + self.extra + '</getResortsResult>')
        return super().get(url, **kwargs)


@pytest.mark.parametrize('destination,expected', [('Сочи, Россия', True), (' Сочи , россия ', True),
    ('Сочи, Турция', False), ('Неизвестный, Россия', False), ('Сочи, Россия, Турция', False)])
def test_miniapp_qualified_destination_stays_within_named_country(destination, expected):
    session = Session()
    result = alean.search_tours(settings(), session, info(destination=destination))
    calls = [kwargs['params'] for _, kwargs in session.calls if kwargs['params']['action'] == 'GetTours']
    assert bool(result.offers) is expected
    assert bool(calls) is expected
    if expected:
        assert calls[0]['resortCIDs'] == 'SOCHI-CID'
        assert calls[0]['countryCID'] == 'RU-CID'


def test_qualified_destination_resolves_duplicate_resort_names_only_inside_country():
    countries = [{'id':'1','name':'Россия'},{'id':'2','name':'Другая страна'}]
    resorts = [{'id':'3','countryId':'1','name':'Курорт'},{'id':'4','countryId':'2','name':'Курорт'}]
    assert alean._resolve_destination(countries, resorts, 'Курорт, Россия') == (countries[0],resorts[0])
    assert alean._resolve_destination(countries, resorts + [dict(resorts[0],id='5')], 'Курорт, Россия') == (None,None)


def test_wide_search_covers_every_requested_date_and_night_once():
    session = Session()
    query = info(dates="Даты гибкие", nights="5-10")
    result = alean.search_tours(settings(), session, query)
    window = tv.resolve_search_window(query['dates'], nights_raw=query['nights'])
    expected = {(date.fromisoformat(window.date_from) + timedelta(days=i), n)
                for i in range((date.fromisoformat(window.date_to)-date.fromisoformat(window.date_from)).days+1)
                for n in range(5, 11)}
    actual = []
    for _, kwargs in session.calls:
        p = kwargs['params']
        if p['action'] != 'GetTours':
            continue
        from datetime import datetime
        start = datetime.strptime(p['dateFrom'], '%d.%m.%Y').date()
        end = datetime.strptime(p['dateTo'], '%d.%m.%Y').date()
        assert (end-start).days <= 3 and p['nightsMax']-p['nightsMin'] <= 3
        actual.extend((start+timedelta(days=i), n) for i in range((end-start).days+1)
                      for n in range(p['nightsMin'], p['nightsMax']+1))
        assert p['adults'] == 2 and p['kidsAges'] == '0,15' and p['priceMax'] == 100000
    assert set(actual) == expected and len(actual) == len(expected)
    assert not result.error and not result.partial
    assert result.completed_windows == result.total_windows == 12
    assert len({o.tour_id for o in result.offers}) == len(result.offers)


@pytest.mark.parametrize('all_failed', [False, True])
def test_partial_ranges_never_claim_complete_or_empty_inventory(monkeypatch, all_failed):
    original, count, gate = alean._request, [], Lock()
    def request(config, session, action, params=None):
        if action == 'GetTours':
            with gate:
                count.append(1)
                fail = all_failed or len(count) == 2
            if fail:
                raise requests.Timeout('private-secret')
        return original(config, session, action, params)
    monkeypatch.setattr(alean, '_request', request)
    result = alean.search_tours(settings(), Session(), info(dates='Даты гибкие', nights='7'))
    assert result.partial and result.warning and result.total_windows == 6
    assert result.completed_windows == (0 if all_failed else 5)
    assert bool(result.error) is all_failed and bool(result.offers) is not all_failed
    assert 'private-secret' not in result.warning + result.error


def test_range_cancellation_stops_before_another_supplier_window():
    session = Session()
    def cancelled():
        return any(k['params']['action'] == 'GetTours' for _, k in session.calls)
    result = alean.search_tours(settings(), session, info(dates='Даты гибкие'), cancelled=cancelled)
    assert result.error_code == 'cancelled'
    assert 1 <= sum(k['params']['action'] == 'GetTours' for _, k in session.calls) <= 3


@pytest.mark.parametrize('status', [401, 403, 429])
def test_range_does_not_repeat_permanent_or_rate_limit_errors(status):
    session = Session(bad={'GetTours': Response('', status=status)})
    result = alean.search_tours(settings(), session, info(dates='Даты гибкие'))
    assert result.partial and result.error and result.completed_windows == 0
    assert 1 <= sum(k['params']['action'] == 'GetTours' for _, k in session.calls) <= 3


def test_cancel_during_cold_catalog_stops_the_next_catalog():
    session = Session()
    result = alean.search_tours(settings(), session, info(), cancelled=lambda: bool(session.calls))
    assert result.error_code == 'cancelled'
    assert [k['params']['action'] for _, k in session.calls] == ['GetCountries']


def test_range_connect_and_read_timeouts_fit_remaining_budget(monkeypatch):
    original, clock, limits = alean._request, [0], []
    monkeypatch.setattr(alean.time, 'monotonic', lambda: clock[0])
    def request(config, session, action, params=None):
        if action == 'GetTours':
            limits.append(config.total_timeout)
            if len(limits) == 1:
                clock[0] = 59.8
        return original(config, session, action, params)
    monkeypatch.setattr(alean, '_request', request)
    alean.search_tours(settings(), Session(), info(dates='Даты гибкие'))
    assert all(total <= .201 for total in limits[1:])


def test_range_deadline_returns_partial_even_if_remaining_windows_are_empty(monkeypatch):
    original = alean._request
    clock = [0]
    monkeypatch.setattr(alean.time, 'monotonic', lambda: clock[0])
    def request(config, session, action, params=None):
        result = original(config, session, action, params)
        if action == 'GetTours':
            clock[0] = 61
        return result
    monkeypatch.setattr(alean, '_request', request)
    result = alean.search_tours(settings(), Session(), info(dates='Даты гибкие'))
    assert result.partial and result.completed_windows == 1 and result.total_windows == 6


def test_range_parallelism_is_bounded_and_covers_the_second_wave():
    entered, release, gate = Event(), Event(), Lock()
    active, peak = [0], [0]
    class DelayedSession(Session):
        def get(self, url, **kwargs):
            if kwargs['params']['action'] != 'GetTours':
                return super().get(url, **kwargs)
            with gate:
                active[0] += 1
                peak[0] = max(peak[0], active[0])
                if active[0] == 3:
                    entered.set()
            try:
                assert release.wait(5)
                return super().get(url, **kwargs)
            finally:
                with gate:
                    active[0] -= 1
    with ThreadPoolExecutor(max_workers=1) as pool:
        task = pool.submit(alean.search_tours, settings(), DelayedSession(), info(dates='Даты гибкие'))
        try:
            assert entered.wait(3), 'supplier windows still run sequentially'
        finally:
            release.set()
        result = task.result(timeout=5)
    assert peak[0] == 3 and result.completed_windows == result.total_windows == 6


@pytest.mark.parametrize('over_budget', [False, True])
def test_progress_exposes_validated_offers_before_remaining_windows_finish(over_budget):
    first, release, gate = Event(), Event(), Lock()
    started, snapshots = [0], []
    class Delayed(Session):
        def get(self, url, **kwargs):
            if kwargs['params']['action'] == 'GetTours':
                with gate:
                    started[0] += 1
                    number = started[0]
                if number > 1:
                    assert release.wait(5)
            return super().get(url, **kwargs)
    def progress(result):
        snapshots.append(result)
        first.set()
    with ThreadPoolExecutor(max_workers=1) as pool:
        task = pool.submit(alean.search_tours, settings(), Delayed(change={'price':'200000'} if over_budget else {}),
                           info(dates='Даты гибкие'), on_progress=progress)
        try:
            assert first.wait(3)
            assert not task.done()
            assert snapshots[0].completed_windows == 1 and snapshots[0].total_windows == 6
            assert bool(snapshots[0].offers) is (not over_budget)
            assert snapshots[0].partial
        finally:
            release.set()
        result = task.result(timeout=5)
    assert not result.partial and result.completed_windows == 6
    assert snapshots[0].completed_windows == 1  # immutable earlier snapshot


def test_progress_consumer_failure_cannot_fail_supplier_search():
    def broken(result):
        raise RuntimeError('fixture-secret')
    result = alean.search_tours(settings(), Session(), info(dates='Даты гибкие'), on_progress=broken)
    assert result.offers and not result.error and result.completed_windows == 6


@pytest.mark.parametrize('raw,expected', [('60',60),('120',120),('29',60),('nan',60),('bad',60)])
def test_range_timeout_setting_is_bounded(monkeypatch, raw, expected):
    monkeypatch.setenv('ALEAN_RANGE_TIMEOUT_SECONDS', raw)
    assert alean.AleanSettings.from_env().range_timeout == expected


def test_parallel_workers_own_and_close_sessions_without_changing_bot_transport(monkeypatch):
    closed, used = [], []
    class TrackedSession(requests.Session):
        def close(self):
            closed.append(id(self))
            super().close()
    monkeypatch.setattr(alean.requests, 'Session', TrackedSession)
    def request(config, transport, action, params=None):
        if action == 'GetTours':
            used.append(id(transport))
            assert transport.headers['X-Test'] == 'fixture'
            assert transport.proxies['https'] == 'http://proxy.invalid'
            assert transport.adapters['https://'].max_retries.total == 0
            assert config.total_timeout is not None
        return ET.fromstring(Session().get('fixture', params=dict(action=action, **(params or {}))).content)
    monkeypatch.setattr(alean, '_request', request)
    with TrackedSession() as original:
        original.headers['X-Test'] = 'fixture'
        original.proxies['https'] = 'http://proxy.invalid'
        result = alean.search_tours(settings(), original, info(dates='Даты гибкие'))
        assert result.completed_windows == 6
        assert used and id(original) not in used
        assert set(used) <= set(closed) and id(original) not in closed


@pytest.mark.parametrize("resort_id,region", [("3", "Сочи"), ("6", "Адлер"),
    ("7", "Лазаревское"), ("8", "Мамайка"), ("9", "Сочи центр")])
def test_sochi_accepts_only_verified_catalog_members(resort_id, region):
    session = SochiDistricts(resort_id)
    result = alean.search_tours(settings(), session, info(destination="Сочи"))
    assert len(result.offers) == 1 and result.offers[0].region == region
    assert result.offers[0].price == 24830.72
    assert session.calls[-1][1]["params"]["resortCIDs"] == "SOCHI-CID"


@pytest.mark.parametrize("resort_id", ["10", "11", "unknown"])
def test_sochi_rejects_unrelated_foreign_and_unknown_resorts(resort_id):
    assert not alean.search_tours(settings(), SochiDistricts(resort_id), info(destination="Сочи")).offers


def test_explicit_adler_remains_strict_and_does_not_expand_to_sochi():
    session = SochiDistricts("6")
    assert alean.search_tours(settings(), session, info(destination="Адлер")).offers
    assert session.calls[-1][1]["params"]["resortCIDs"] == "ADLER-CID"
    assert not alean.search_tours(settings(), SochiDistricts("7"), info(destination="Адлер")).offers


def test_ambiguous_sochi_member_is_not_selected_arbitrarily():
    session = SochiDistricts("6", '<resort id="12" cid="SECOND-ADLER" countryId="1" name="Адлер"/>')
    assert not alean.search_tours(settings(), session, info(destination="Сочи")).offers


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


@pytest.mark.parametrize("value,expected", [(None, 30), ("10", 10), ("45.5", 45.5),
    ("60", 60), ("", 30), ("invalid", 30), ("nan", 30), ("inf", 30),
    ("-1", 30), ("9.9", 30), ("60.1", 30)])
def test_search_timeout_environment_is_bounded_and_never_enables_supplier(monkeypatch, value, expected):
    if value is None:
        monkeypatch.delenv("ALEAN_SEARCH_TIMEOUT_SECONDS", raising=False)
    else:
        monkeypatch.setenv("ALEAN_SEARCH_TIMEOUT_SECONDS", value)
    monkeypatch.setenv("ALEAN_ENABLED", "false")
    config = alean.AleanSettings.from_env()
    assert config.search_timeout == expected and config.timeout == 10
    assert config.enabled is False


def test_slow_search_succeeds_without_extending_catalog_or_connect_timeout(monkeypatch):
    class SlowSearch(Session):
        def get(self, url, **kwargs):
            if kwargs["params"]["action"] == "GetTours":
                # Model the observed 14.22-second search without sleeping.
                limit = kwargs["timeout"]
                read_limit = limit[1] if isinstance(limit, tuple) else limit
                if read_limit < 14.22:
                    raise requests.ReadTimeout("never log fixture-secret")
            return super().get(url, **kwargs)
    monkeypatch.delenv("ALEAN_SEARCH_TIMEOUT_SECONDS", raising=False)
    config = alean.AleanSettings.from_env()
    config.enabled, config.username, config.password = True, "fixture-user", "fixture-secret"
    session = SlowSearch()
    assert alean.search_tours(config, session, info()).offers
    assert all(call[1]["timeout"] == 10 for call in session.calls[:-1])
    assert session.calls[-1][1]["timeout"] == (10, 30)


def test_search_read_timeout_preserves_fallback_and_sanitizes_errors(monkeypatch, caplog):
    class TimeoutSearch(Session):
        def get(self, url, **kwargs):
            if kwargs["params"]["action"] == "GetTours":
                assert kwargs["timeout"] == (10, 45)
                raise requests.ReadTimeout("fixture-secret private-url")
            return super().get(url, **kwargs)
    config = router.ProviderSettings(order=("alean", "tourvisor"), alean=settings(search_timeout=45),
                                    tourvisor=tv.TourvisorSettings(enabled=True, token="token"))
    expected = tv.SearchResult(offers=[tv.TourOffer("Fallback", 0, "", "", 7, "", "", "", 90000)])
    monkeypatch.setattr(tv, "search_tours", lambda *args, **kwargs: expected)
    result, provider = router.search_tours(config, TimeoutSearch(), info())
    assert result is expected and provider == "tourvisor"
    assert "fixture-secret" not in caplog.text and "private-url" not in caplog.text


@pytest.mark.parametrize("failure", [False, True])
def test_smoke_audit_records_timeout_and_safe_timing_on_success_and_failure(monkeypatch, failure):
    from scripts.alean_live_smoke import AuditSession
    ticks = iter([10, 24.22])
    monkeypatch.setattr("scripts.alean_live_smoke.time.monotonic", lambda: next(ticks))
    response = Response("<getToursResult/>")
    def get(*args, **kwargs):
        if failure:
            raise requests.ReadTimeout("fixture-secret private-url")
        return response
    monkeypatch.setattr(requests.Session, "get", get)
    with AuditSession() as session:
        kwargs = dict(params={"action": "GetTours"}, timeout=(10, 30), auth=("fixture-user", "fixture-secret"))
        if failure:
            with pytest.raises(requests.ReadTimeout):
                session.get(alean.ENDPOINT, **kwargs)
        else:
            assert session.get(alean.ENDPOINT, **kwargs) is response
        report = json.loads(json.dumps(session.operations))
    expected = {"action": "GetTours", "timeoutSeconds": [10, 30], "responseHeadersSeconds": 14.22}
    expected.update({"errorType": "ReadTimeout"} if failure else {"status": 200})
    assert report == [expected]
    assert "fixture-secret" not in str(report) and "private-url" not in str(report)


def test_resort_and_hotel_search_do_not_broaden_the_request():
    session = Session()
    assert alean.search_tours(settings(),session,info(destination="Сочи",hotel_query="Тестовый отель")).offers
    params = session.calls[-1][1]["params"]
    assert params["resortCIDs"] == "SOCHI-CID" and params["hotelCIDs"] == "HOTEL-CID"
    assert not alean.search_tours(settings(),session,info(destination="Соч")).offers


def test_live_sapi_fractional_price_survives_search_json_and_cards():
    # Price format observed in the authenticated SAPI response on 2026-10-12.
    import vk_bot
    result = alean.search_tours(settings(), Session(change={"price": "24830.72"}),
                                info(kids_ages=[7], budget=24831))
    assert len(result.offers) == 1
    selected = json.loads(json.dumps(asdict(result.offers[0]), ensure_ascii=False))
    assert selected["price"] == 24830.72
    assert "24 830.72 ₽" in tv.format_client_message(result)
    assert "24 830.72 ₽" in vk_bot._selected_tour_summary({"selected_tour": selected})
    actual = alean.actualize_tour(selected)
    assert actual["confirmed"] is False and actual["total_price"] == 24830.72
    assert json.loads(json.dumps(actual))["total_price"] == 24830.72


def test_fractional_price_above_integer_cap_is_not_rounded_into_budget():
    result = alean.search_tours(settings(), Session(change={"price": "24830.72"}), info(budget=24830))
    assert not result.offers


@pytest.mark.parametrize("field", ["price", "fuel_charge"])
@pytest.mark.parametrize("value", ["NaN", "Infinity", "-Infinity", "invalid", -1, True])
def test_actualization_fallback_rejects_invalid_stored_amounts(field, value):
    offer = {"price": 24830.72, "fuel_charge": 100.15, "currency": "RUB",
             "provider": "alean", "confirmed": True}
    offer[field] = value
    result = alean.actualize_tour(offer)
    assert result["confirmed"] is False
    assert result["status"] == "unknown"
    assert result["total_price"] == 0
    assert "SOAP Specify" in result["hotel_status"]
    json.dumps(result, allow_nan=False)


def test_actualization_fallback_preserves_valid_fractional_total():
    result = alean.actualize_tour({"price": 24830.72, "fuel_charge": 100.15,
                                  "currency": "RUB", "provider": "alean"})
    assert result["total_price"] == 24930.87
    assert result["confirmed"] is False
    assert result["currency"] == "RUB"
    json.dumps(result, allow_nan=False)


@pytest.mark.parametrize("price", ["NaN", "Infinity", "-Infinity", "1e10000"])
def test_nonfinite_or_unrepresentable_supplier_price_is_rejected(price):
    result = alean.search_tours(settings(), Session(change={"price": price}), info(budget_open_ended=True))
    assert not result.offers


def test_vk_price_sorting_and_fuel_charge_preserve_kopecks():
    import vk_bot
    lower = {"price": 24830.72, "fuel_charge": 100.15}
    higher = {"price": 24830.99, "fuel_charge": 100.15}
    assert vk_bot._offer_total_price(lower) == 24930.87
    assert vk_bot._offer_total_price(lower) < vk_bot._offer_total_price(higher)
    assert vk_bot._compact_tour_price(lower) == "24 930.87 ₽"
    assert sorted([higher, lower], key=vk_bot._offer_total_price) == [lower, higher]


@pytest.mark.parametrize("updates", [dict(direct_only=True),dict(people=0),dict(kids_ages=[18]),
    dict(kids_ages=[True]),dict(nights="50"),dict(nights=0),dict(nights="10-7"),
    dict(budget=-1),dict(budget=2147483648),dict(dates="неизвестные даты")])
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


def test_unsupported_departure_is_distinct_from_destination_and_never_searches():
    session = Session()
    result = alean.search_tours(settings(), session, info(origin='Архангельск'))
    assert result.error_code == 'departure_unsupported' and not result.offers
    assert all(call[1]['params']['action'] != 'GetTours' for call in session.calls)
    routed, _ = router.search_tours(router.ProviderSettings(order=('alean',), alean=settings()), Session(), info(origin='Архангельск'))
    assert routed.error_code == 'departure_unsupported'
    result = alean.search_tours(settings(), Session(), info(destination='Несуществующий курорт'))
    assert result.error_code == 'destination_unsupported'


@pytest.mark.parametrize('rows', ['', '<city name="Архангельск"/>',
    '<city name="Архангельск" cid="one"/><city name="Архангельск" cid="two"/>'])
def test_invalid_or_ambiguous_departure_catalog_is_not_unsupported(rows):
    result = alean.search_tours(settings(), Session(bad={'GetDepartCities': Response(
        '<getDepartCitiesResult>' + rows + '</getDepartCitiesResult>')}), info(origin='Архангельск'))
    assert result.error and result.error_code == '' and not result.offers


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
