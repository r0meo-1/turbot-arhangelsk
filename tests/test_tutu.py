"""Tests for the Tutu.ru MCP integration.

Deliberately covers the network boundary itself (timeout / 500 / malformed
JSON / JSON-RPC error), which is the failure class a live integration
actually hits and the one the rest of this repo's suite historically mocked
away.
"""

import json
from datetime import date

import pytest
import requests

from shared import tutu


# ---------------------------------------------------------------------------
# Fakes

class FakeResp:
    def __init__(self, payload=None, status=200, bad_json=False):
        self._payload = payload
        self.status_code = status
        self._bad_json = bad_json

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} error")

    def json(self):
        if self._bad_json:
            raise ValueError("Expecting value: line 1 column 1 (char 0)")
        return self._payload


class FakeSession:
    """Minimal stand-in for requests.Session."""

    def __init__(self, resp=None, exc=None):
        self._resp = resp
        self._exc = exc
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if self._exc:
            raise self._exc
        return self._resp


def envelope(payload):
    """Wrap a tool payload the way an MCP server does."""
    return {
        "jsonrpc": "2.0",
        "id": 1,
        "result": {"content": [{"type": "text", "text": json.dumps(payload)}]},
    }


SAMPLE_SEARCH = {
    "offers": [
        {
            "offer_id": "abc",
            "transport": "avia",
            "price": {"amount": 24539.0, "currency": "RUB"},
            "duration_min": 220,
            "carriers": ["Аэрофлот", "Pegasus Airlines"],
            "departure_at": "2026-09-15T13:15:00+03:00",
            "search_results_url": "https://avia.tutu.ru/f/Arh/Hrg/",
            "checkout_url": "https://www.tutu.ru/checkout/abc",
        },
        {
            "offer_id": "def",
            "transport": "avia",
            "price": {"amount": 31000.0, "currency": "RUB"},
            "duration_min": 400,
            "carriers": ["Победа"],
            "departure_at": "2026-09-15T06:30:00+03:00",
            "search_results_url": "https://avia.tutu.ru/f/Arh/Hrg/",
        },
    ],
    "meta": {
        "from": {"name": "Архангельск", "iata": "ARH"},
        "to": {"name": "Хургада", "iata": "HRG"},
    },
}


@pytest.fixture
def settings():
    s = tutu.TutuSettings(enabled=True, timeout=5, max_offers=3)
    tutu.get_cache(s).clear()   # module-level cache must not leak between tests
    yield s
    tutu.get_cache(s).clear()


# ---------------------------------------------------------------------------
# Destination resolution

def test_country_maps_to_resort_not_capital():
    """Египет must resolve to a beach resort, not Cairo (measurably cheaper)."""
    assert tutu.resolve_destination_city("Египет") == "Хургада"
    assert tutu.resolve_destination_city("турция") == "Анталья"
    assert tutu.resolve_destination_city("  ОАЭ ") == "Дубай"


def test_unknown_destination_passes_through():
    assert tutu.resolve_destination_city("Сочи") == "Сочи"
    assert tutu.resolve_destination_city("") == ""


# ---------------------------------------------------------------------------
# Date resolution — the funnel stores fuzzy presets, not ISO dates

def test_free_text_range_uses_existing_parser():
    start, _end = tutu.resolve_dates("15-22 июня 2027", today=date(2026, 8, 1))
    assert start == "2027-06-15"


def test_preset_month_offset():
    got, _ = tutu.resolve_dates("через месяц", today=date(2026, 8, 1))
    assert got == "2026-08-31"


def test_preset_next_weekend_is_a_saturday():
    got, _ = tutu.resolve_dates("ближайшие выходные", today=date(2026, 8, 1))
    assert date.fromisoformat(got).weekday() == 5
    assert date.fromisoformat(got) > date(2026, 8, 1)


def test_season_rolls_to_next_year_when_past():
    got, _ = tutu.resolve_dates("лето", today=date(2026, 8, 1))
    assert got == "2027-07-15"   # July already gone in 2026


def test_past_date_is_rejected():
    """A parsed date in the past must not be sent to the search."""
    got, _ = tutu.resolve_dates("1-5 января 2020", today=date(2026, 8, 1))
    assert got is None


def test_unparseable_dates_return_none():
    assert tutu.resolve_dates("") == (None, None)
    assert tutu.resolve_dates("когда-нибудь потом") == (None, None)


def test_parse_people_variants():
    assert tutu.parse_people("5+") == 5
    assert tutu.parse_people("2") == 2
    assert tutu.parse_people(3) == 3
    assert tutu.parse_people("") == 1
    assert tutu.parse_people("много") == 1
    assert tutu.parse_people(99) == 9      # clamped


# ---------------------------------------------------------------------------
# Transport boundary — the part that actually breaks in production

def test_mcp_call_success(settings):
    session = FakeSession(FakeResp(envelope(SAMPLE_SEARCH)))
    got = tutu.mcp_call(settings, session, "search_avia", {"origin": "Архангельск"})
    assert got["meta"]["to"]["name"] == "Хургада"
    # Stateless protocol: one POST, no initialize handshake.
    assert len(session.calls) == 1
    body = session.calls[0][1]["json"]
    assert body["method"] == "tools/call"
    assert body["params"]["name"] == "search_avia"


def test_mcp_call_sends_explicit_timeout(settings):
    session = FakeSession(FakeResp(envelope(SAMPLE_SEARCH)))
    tutu.mcp_call(settings, session, "search_avia", {})
    assert session.calls[0][1]["timeout"] == settings.timeout


def test_mcp_call_timeout_returns_none(settings):
    session = FakeSession(exc=requests.Timeout("timed out"))
    assert tutu.mcp_call(settings, session, "search_avia", {}) is None


def test_mcp_call_http_500_returns_none(settings):
    session = FakeSession(FakeResp(None, status=500))
    assert tutu.mcp_call(settings, session, "search_avia", {}) is None


def test_mcp_call_malformed_json_returns_none(settings):
    session = FakeSession(FakeResp(bad_json=True))
    assert tutu.mcp_call(settings, session, "search_avia", {}) is None


def test_mcp_call_jsonrpc_error_returns_none(settings):
    session = FakeSession(FakeResp({"jsonrpc": "2.0", "id": 1,
                                    "error": {"code": -32602, "message": "bad params"}}))
    assert tutu.mcp_call(settings, session, "search_avia", {}) is None


def test_mcp_call_tool_is_error_returns_none(settings):
    session = FakeSession(FakeResp({"jsonrpc": "2.0", "id": 1,
                                    "result": {"isError": True,
                                               "content": [{"text": "boom"}]}}))
    assert tutu.mcp_call(settings, session, "search_avia", {}) is None


def test_mcp_call_empty_content_returns_none(settings):
    session = FakeSession(FakeResp({"jsonrpc": "2.0", "id": 1, "result": {"content": []}}))
    assert tutu.mcp_call(settings, session, "search_avia", {}) is None


def test_cache_prevents_second_upstream_call(settings):
    session = FakeSession(FakeResp(envelope(SAMPLE_SEARCH)))
    args = {"origin": "Архангельск", "destination": "Хургада"}
    tutu.mcp_call(settings, session, "search_avia", args)
    tutu.mcp_call(settings, session, "search_avia", args)
    assert len(session.calls) == 1, "identical search should be served from cache"


# ---------------------------------------------------------------------------
# Search orchestration

def _stub(payloads):
    """request_fn returning queued payloads; records the arguments it saw."""
    seen = []

    def fn(tool, args):
        seen.append((tool, args))
        return payloads.pop(0) if payloads else None

    fn.seen = seen
    return fn


def test_search_offers_happy_path(settings):
    fn = _stub([SAMPLE_SEARCH])
    result = tutu.search_offers(
        settings, FakeSession(),
        destination="Египет", dates_raw="через месяц",
        origin="Архангельск", people="2", budget=60000,
        request_fn=fn,
    )
    assert result is not None
    assert result.to_city == "Хургада"
    assert result.cheapest.price == 24539.0
    tool, args = fn.seen[0]
    assert tool == "search_avia"
    assert args["destination"] == "Хургада"    # country was mapped
    assert args["adults"] == 2
    assert args["price_max"] == 120000    # 60 000 per person × 2


def test_search_arguments_match_server_schema(settings):
    """Guard against inventing a parameter name.

    A mock happily accepts `passengers`; the real server rejects it with a
    validation error. This test pins every argument we send to the schema
    the server actually publishes.
    """
    fn = _stub([SAMPLE_SEARCH])
    tutu.search_offers(
        settings, FakeSession(),
        destination="Египет", dates_raw="15-22 июня 2027",
        origin="Архангельск", people="3", budget=90000,
        request_fn=fn,
    )
    _tool, args = fn.seen[0]
    unknown = set(args) - tutu.SEARCH_ARGS
    assert not unknown, f"arguments not in the server schema: {unknown}"


def test_round_trip_sends_return_date(settings):
    fn = _stub([SAMPLE_SEARCH])
    tutu.search_offers(
        settings, FakeSession(), destination="Египет",
        dates_raw="15-22 июня 2027", request_fn=fn,
    )
    _tool, args = fn.seen[0]
    assert args["departure_date"] == "2027-06-15"
    assert args["return_date"] == "2027-06-22"


def test_search_offers_disabled_returns_none():
    off = tutu.TutuSettings(enabled=False)
    assert tutu.search_offers(off, FakeSession(), destination="Египет",
                              dates_raw="через месяц") is None


def test_search_offers_skips_when_dates_unresolvable(settings):
    fn = _stub([SAMPLE_SEARCH])
    result = tutu.search_offers(settings, FakeSession(), destination="Египет",
                                dates_raw="ну когда-нибудь", request_fn=fn)
    assert result is None
    assert fn.seen == [], "must not call upstream without a usable date"


def test_search_offers_retries_without_budget_when_empty(settings):
    """A too-tight budget must not leave the manager blind to the market."""
    fn = _stub([{"offers": [], "meta": {}}, SAMPLE_SEARCH])
    result = tutu.search_offers(
        settings, FakeSession(), destination="Египет", dates_raw="через месяц",
        budget=1000, request_fn=fn,
    )
    assert result is not None
    assert result.over_budget is True
    assert len(fn.seen) == 2
    assert "price_max" in fn.seen[0][1]
    assert "price_max" not in fn.seen[1][1]


def test_budget_cap_is_multiplied_by_party_size():
    """Budget is per person; Tutu caps the whole offer."""
    s = tutu.TutuSettings(enabled=True)
    tutu.get_cache(s).clear()
    fn = _stub([SAMPLE_SEARCH])
    tutu.search_offers(s, FakeSession(), destination="Египет",
                       dates_raw="через месяц", people="3", budget=50000,
                       request_fn=fn)
    assert fn.seen[0][1]["price_max"] == 150000


def test_total_budget_is_not_multiplied_by_party_size():
    s = tutu.TutuSettings(enabled=True)
    tutu.get_cache(s).clear()
    fn = _stub([SAMPLE_SEARCH])
    tutu.search_offers(
        s, FakeSession(), destination="Египет", dates_raw="через месяц",
        people="3", budget=200000, budget_is_total=True, request_fn=fn,
    )
    assert fn.seen[0][1]["price_max"] == 200000


def test_over_budget_client_message_is_honest():
    """Never quote a client a price several times their stated budget."""
    res = _result()
    res.over_budget = True
    text = tutu.format_client_message(res)
    assert "не нашлось" in text
    assert "24 539 ₽" in text          # the honest market floor is still shown
    assert "Ориентир по перелёту" not in text


def test_over_budget_warns_the_manager():
    res = _result()
    res.over_budget = True
    text = tutu.format_admin_block(res)
    assert "не покрывает" in text


def test_search_offers_returns_none_on_transport_failure(settings):
    fn = _stub([None])
    assert tutu.search_offers(settings, FakeSession(), destination="Египет",
                              dates_raw="через месяц", request_fn=fn) is None


# ---------------------------------------------------------------------------
# Rendering — the two audiences get deliberately different payloads

def _result():
    return tutu.SearchResult(
        offers=tutu._normalise_offers(SAMPLE_SEARCH, 3),
        from_city="Архангельск", to_city="Хургада",
        search_url="https://avia.tutu.ru/f/Arh/Hrg/",
        depart_date="2026-09-15",
    )


def test_client_message_shows_prices_and_search_link():
    text = tutu.format_client_message(_result())
    assert "24 539 ₽" in text
    assert "Архангельск" in text and "Хургада" in text
    assert "avia.tutu.ru" in text


def test_client_message_never_leaks_checkout_link():
    """Handing the client a buy button routes the sale around the agency."""
    text = tutu.format_client_message(_result())
    assert "checkout" not in text


def test_client_message_escapes_html():
    res = _result()
    res.to_city = "<b>Хургада</b>"
    text = tutu.format_client_message(res)
    assert "&lt;b&gt;" in text


def test_admin_block_has_anchor_and_checkout():
    text = tutu.format_admin_block(_result())
    assert "от 24 539 ₽" in text
    assert "https://www.tutu.ru/checkout/abc" in text
    assert "2026-09-15" in text


def test_identical_fare_families_are_collapsed():
    """Tutu returns one entry per fare family; three identical lines in a row
    reads as a broken bot, so same price + carrier + departure collapses."""
    payload = {
        "offers": [
            {"price": {"amount": 86396.0, "currency": "RUB"}, "carriers": ["Pegasus"],
             "departure_at": "2026-09-15T15:05:00+03:00", "duration_min": 825},
            {"price": {"amount": 86396.0, "currency": "RUB"}, "carriers": ["Pegasus"],
             "departure_at": "2026-09-15T15:05:00+03:00", "duration_min": 825},
            {"price": {"amount": 86396.0, "currency": "RUB"}, "carriers": ["Pegasus"],
             "departure_at": "2026-09-15T15:05:00+03:00", "duration_min": 825},
            {"price": {"amount": 91000.0, "currency": "RUB"}, "carriers": ["Аэрофлот"],
             "departure_at": "2026-09-15T06:00:00+03:00", "duration_min": 700},
        ],
        "meta": {},
    }
    offers = tutu._normalise_offers(payload, 3)
    assert len(offers) == 2
    assert [o.price for o in offers] == [86396.0, 91000.0]


def test_search_over_fetches_to_survive_dedup(settings):
    fn = _stub([SAMPLE_SEARCH])
    tutu.search_offers(settings, FakeSession(), destination="Египет",
                       dates_raw="через месяц", request_fn=fn)
    assert fn.seen[0][1]["page_size"] > settings.max_offers
    assert fn.seen[0][1]["page_size"] <= 30


def test_empty_result_renders_nothing():
    empty = tutu.SearchResult()
    assert tutu.format_client_message(empty) == ""
    assert tutu.format_admin_block(empty) == ""


def test_plain_markup_has_no_html():
    """VK renders no markup — HTML tags would be shown to the client verbatim."""
    text = tutu.format_client_message(_result(), markup="plain")
    assert "<b>" not in text and "</a>" not in text and "&" not in text
    assert "24 539 ₽" in text
    assert "avia.tutu.ru" in text          # link still reachable, just bare
    assert "checkout" not in text          # same rule as Telegram


def test_html_markup_still_used_by_default():
    text = tutu.format_client_message(_result())
    assert "<b>" in text and "<a href=" in text


def test_plain_markup_over_budget_message():
    res = _result(); res.over_budget = True
    text = tutu.format_client_message(res, markup="plain")
    assert "<b>" not in text
    assert "не нашлось" in text
