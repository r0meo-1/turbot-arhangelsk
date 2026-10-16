"""Telegram package search stays private, optional and off the lead-save path."""
import os
import pytest

os.environ.setdefault("BOT_TOKEN", "dummy-token")
os.environ["SYNC_COMPLETION"] = "true"
os.environ["CONSENT_MODE"] = "strict"
import bot
from shared import tourvisor as tv


@pytest.fixture
def enabled(monkeypatch):
    monkeypatch.setenv("ALEAN_ENABLED", "true")
    monkeypatch.setenv("ALEAN_USERNAME", "fixture")
    monkeypatch.setenv("ALEAN_PASSWORD", "fixture-secret")
    monkeypatch.setenv("TOUR_PROVIDER_ORDER", "alean,travelata")


@pytest.mark.parametrize("dates,trip", [("15-22 ноября", True), ("15 ноября", False)])
def test_search_preserves_party_budget_and_date_semantics(monkeypatch, enabled, dates, trip):
    captured = {}
    def search(settings, session, query, **kwargs):
        captured.update(query)
        return tv.SearchResult()
    monkeypatch.setattr(bot._alean, "search_tours", search)
    source = dict(dates=dates, nights=9, people=2, kids_ages=[1,15], budget=100000,
                  budget_scope="total", phone="private", chat_id=123, name="private")
    bot._alean_search(source)
    assert captured == dict(dates=dates, nights=9, people=2, kids_ages=[1,15],
                            budget=100000, budget_scope="total", dates_are_trip=trip)
    assert "dates_are_trip" not in source


@pytest.mark.parametrize("key,value", [("ALEAN_ENABLED","false"), ("ALEAN_USERNAME",""),
    ("ALEAN_PASSWORD",""), ("TOUR_PROVIDER_ORDER","travelata,tourvisor")])
def test_explicit_activation_required(monkeypatch, enabled, key, value):
    monkeypatch.setenv(key,value)
    monkeypatch.setattr(bot._alean, "search_tours", lambda *a, **k: pytest.fail("disabled supplier called"))
    assert bot._alean_search({}) is None


def test_unexpected_error_is_sanitized(monkeypatch, enabled, caplog):
    def fail(*args, **kwargs):
        raise RuntimeError("fixture-secret")
    monkeypatch.setattr(bot._alean, "search_tours", fail)
    assert bot._alean_search({}) is None
    assert "fixture-secret" not in caplog.text


@pytest.mark.parametrize("has_offers", [True, False])
def test_completion_delivers_package_or_falls_back(monkeypatch, has_offers):
    calls = []
    offer = tv.TourOffer("Отель <b> & " + "x"*4000, 4, "Сочи", "2026-11-15", 7,
                        "HB", "Стандарт", "Алеан", 80000, provider="alean")
    monkeypatch.setattr(bot, "send_lead_to_mdt", lambda *a: calls.append("crm"))
    monkeypatch.setattr(bot, "_alean_search", lambda *a: tv.SearchResult(offers=[offer] if has_offers else []))
    monkeypatch.setattr(bot, "_tutu_search", lambda *a: calls.append("tutu"))
    monkeypatch.setattr(bot, "_send_ai_blurb", lambda *a: calls.append("ai"))
    monkeypatch.setattr(bot, "LEAD_NOTIFY_IDS", [999])
    sent = []
    monkeypatch.setattr(bot, "send_message", lambda *a, **kw: sent.append((a,kw)))
    bot._post_completion_side_effects(123, {}, "private-phone", "Клиент")
    assert calls == (["crm"] if has_offers else ["crm", "tutu", "ai"])
    if has_offers:
        assert [a[0] for a,kw in sent] == [123,999]
        for args, kwargs in sent:
            assert not kwargs.get("parse_mode")
            assert len(args[1]) < 4096
            assert "трансфер не включён" in args[1]
            assert "проверки менеджером" in args[1]
        assert "chat 123" in sent[1][0][1]
        assert "SOAP Specify" in sent[1][0][1]
    else:
        assert sent == []


@pytest.mark.parametrize("scope,minimum", [("total", 100000), ("per_person", 400000)])
def test_open_ended_budget_excludes_below_minimum_with_exact_children(monkeypatch, enabled, scope, minimum):
    def offer(price, charge=0, currency="RUB"):
        return tv.TourOffer("Отель", 3, "Сочи", "2099-11-15", 7, "HB", "Номер", "Алеан", price,
                            fuel_charge=charge, currency=currency)
    result = tv.SearchResult(offers=[offer(minimum-1), offer(minimum-500, 500),
                                    offer(minimum+1), offer(minimum, currency="EUR")])
    monkeypatch.setattr(bot._alean, "search_tours", lambda *a, **k: result)
    actual = bot._alean_search(dict(budget=100000, budget_scope=scope, budget_open_ended=True,
                                   people=2, kids_ages=[1,15]))
    assert [(o.price, o.fuel_charge) for o in actual.offers] == [(minimum-500,500), (minimum+1,0)]


def test_progress_also_obeys_open_ended_budget(monkeypatch, enabled):
    seen = []
    def search(*args, **kwargs):
        result = tv.SearchResult(offers=[tv.TourOffer("Отель",3,"Сочи","2099-11-15",7,"HB","Номер","Алеан",99999)])
        kwargs['on_progress'](result)
        return result
    monkeypatch.setattr(bot._alean, 'search_tours', search)
    bot._alean_search(dict(budget=100000,budget_scope='total',budget_open_ended=True),on_progress=lambda r:seen.append(list(r.offers)))
    assert seen == [[]]


@pytest.mark.parametrize('scope,expected', [('total',100000), ('per_person',400000)])
def test_minimum_budget_is_sent_to_supplier_as_total_rubles(monkeypatch, enabled, scope, expected):
    captured = {}
    def search(settings, session, query, **kwargs):
        captured.update(query)
        return tv.SearchResult()
    monkeypatch.setattr(bot._alean, 'search_tours', search)
    bot._alean_search(dict(people=2, kids_ages=[1,15], budget=100000,
                          budget_scope=scope, budget_open_ended=True))
    assert captured['budget_min_rub'] == expected
