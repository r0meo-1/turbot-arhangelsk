from datetime import date, timedelta
import itertools
import json
import threading

import pytest

from shared import alean_readonly as search
from shared import tourvisor as tv
from shared.telegram_alean_search import AleanDialog
from shared.dates import parse_russian_dates
from shared.validation import parse_kids_ages


def query(**updates):
    start = date.today() + timedelta(days=30)
    return dict(kind="tour", destination="Сочи", arrival=start.isoformat(),
                departure=(start + timedelta(days=7)).isoformat(), adults=2,
                children_ages=[7], budget=150000, origin="Москва", **updates)


def test_existing_date_and_age_formats_keep_whitespace_tolerant_parsing():
    assert parse_russian_dates("1" + " " * 80 + "—" + " " * 80 + "2 июня 2030") == ("2030-06-01", "2030-06-02")
    assert parse_kids_ages("5" + " " * 200 + "и" + " " * 200 + "9") == (True, [5, 9], "")


@pytest.mark.parametrize("kind", ["hotel", "tour"])
def test_demo_has_fictional_results_and_no_supplier_calls(monkeypatch, kind):
    monkeypatch.setattr(search.alean, "search_tours", lambda *a, **kw: pytest.fail("supplier called"))
    raw = query(); raw["kind"] = kind
    result = search.search(raw)
    assert len(result["offers"]) == 3
    assert "вымышленные" in result["notice"]
    assert not result["booking_enabled"] and not result["submission_enabled"]
    for offer in result["offers"]:
        assert offer["mocked"] and not offer["confirmed"] and offer["booking_url"] is None
        assert offer["location"] == "Сочи" and offer["currency"] == "RUB"
    json.dumps(result, allow_nan=False)


@pytest.mark.parametrize("field,value", [("adults", True), ("adults", 0), ("adults", 9),
    ("budget", -1), ("budget", "NaN"), ("children_ages", [18]), ("children_ages", [True]),
    ("children_ages", [7]*7), ("arrival", "bad"), ("departure", "2020-01-01"),
    ("destination", "Турция"), ("kind", "cruise")])
def test_invalid_search_is_rejected(field, value):
    raw = query(); raw[field] = value
    with pytest.raises(search.ValidationError):
        search.search(raw)


@pytest.mark.parametrize("scenario,state", [("empty", "empty"), ("timeout", "timeout"), ("error", "error")])
def test_demo_failure_states(scenario, state):
    result = search.search(query(), scenario=scenario)
    assert result["state"] == state and not result["offers"]
    assert result["mode"] == "demo"


def test_budget_filters_results():
    raw = query(); raw["budget"] = 1
    assert search.search(raw)["state"] == "empty"


def test_live_requires_explicit_activation_and_never_silently_uses_mocks(monkeypatch):
    monkeypatch.setenv("ALEAN_READONLY_LIVE_ENABLED", "false")
    monkeypatch.setattr(search.alean, "search_tours", lambda *a, **kw: pytest.fail("supplier called"))
    result = search.search(query(), mode="live")
    assert result["state"] == "unavailable" and not result["offers"]
    assert result["mode"] == "live" and "ДЕМО" not in result["notice"]


def enable(monkeypatch):
    for key, value in dict(ALEAN_READONLY_LIVE_ENABLED="true", ALEAN_ENABLED="true",
                           ALEAN_USERNAME="fixture", ALEAN_PASSWORD="fixture-secret").items():
        monkeypatch.setenv(key, value)


def test_live_boundary_excludes_contact_data_and_stays_unconfirmed(monkeypatch):
    enable(monkeypatch)
    raw = query(); raw.update(phone="fictional", chat_id=123, name="fictional")
    def supplier(settings, session, supplier_query):
        assert not {"phone", "chat_id", "name"}.intersection(supplier_query)
        assert supplier_query["dates_are_trip"] is False and supplier_query["kids_ages"] == [7]
        return tv.SearchResult(offers=[tv.TourOffer("Fixture hotel", 4, "Сочи", raw["arrival"], 7,
            "HB", "Стандарт", "Алеан", 24830.72, provider="alean")])
    monkeypatch.setattr(search.alean, "search_tours", supplier)
    result = search.search(raw, mode="live", session=object())
    assert result["offers"][0]["price"] == 24830.72
    assert not result["offers"][0]["confirmed"] and not result["offers"][0]["mocked"]
    assert result["offers"][0]["booking_url"] is None


def test_unverified_live_hotels_are_unavailable(monkeypatch):
    enable(monkeypatch)
    monkeypatch.setattr(search.alean, "search_tours", lambda *a, **kw: pytest.fail("supplier called"))
    raw = query(); raw["kind"] = "hotel"
    assert search.search(raw, mode="live")["state"] == "unavailable"


@pytest.mark.parametrize("failure,state", [("Alean: timeout", "timeout"), ("fixture-secret", "error")])
def test_live_failure_is_sanitized(monkeypatch, failure, state):
    enable(monkeypatch)
    monkeypatch.setattr(search.alean, "search_tours", lambda *a: tv.SearchResult(error=failure))
    result = search.search(query(), mode="live", session=object())
    assert result["state"] == state and "fixture-secret" not in json.dumps(result)


@pytest.fixture
def bot_demo(monkeypatch):
    import bot
    monkeypatch.setattr(bot, "_seen_update_ids", {})
    monkeypatch.setattr(bot, "_alean_dialog", AleanDialog())
    monkeypatch.setattr(bot, "save_state", lambda: None)
    for name in ("save_lead", "_notify_admin", "send_lead_to_mdt", "_touch_user", "_accept_miniapp_trip"):
        monkeypatch.setattr(bot, name, lambda *a, **kw: pytest.fail("lead/user/manager path entered"))
    monkeypatch.setattr(bot, "answer_callback", lambda *a, **kw: None)
    sent = []
    monkeypatch.setattr(bot, "send_message", lambda uid, text, **kw: sent.append((uid, text, kw)))
    return bot, sent


def full_dialog(bot, sent, *, kind="tour"):
    ids = itertools.count(900000)
    chat = 770001
    def message(text):
        bot.dispatch_update({"update_id": next(ids), "message": {"chat": {"id": chat}, "text": text}}, strict=True)
    def callback(action):
        token = bot._alean_dialog.sessions[chat]["token"]
        bot.dispatch_update({"update_id": next(ids), "callback_query": {"id": "fictional",
            "data": f"as:{token}:{action}", "message": {"chat": {"id": chat}}}}, strict=True)
    message("/alean")
    callback(kind); callback("sochi")
    raw = query()
    for value in (raw["arrival"], raw["departure"], "2", "7", "150000"):
        message(value)
    if kind == "tour":
        message("Москва")
    return chat, message, callback


@pytest.mark.parametrize("kind", ["hotel", "tour"])
def test_complete_telegram_demo_back_cancel_and_stale_buttons(bot_demo, kind):
    bot, sent = bot_demo
    chat, message, callback = full_dialog(bot, sent, kind=kind)
    old_token = bot._alean_dialog.sessions[chat]["token"]
    callback("back")
    assert bot._alean_dialog.sessions[chat]["query"]["destination"] == "Сочи"
    callback("keep"); callback("search")
    assert any("вымышленный" in text for _, text, _ in sent)
    assert all(uid == chat for uid, _, _ in sent)
    bot._alean_dialog.callback(chat, f"as:{old_token}:search", bot.send_message)
    assert "устарела" in sent[-1][1]
    callback("edit")
    assert bot._alean_dialog.sessions[chat]["query"]["children_ages"] == [7]
    message("/cancel")
    assert not bot._alean_dialog.active(chat)


@pytest.mark.parametrize("scenario,text", [("empty", "не найдено"), ("timeout", "истекло"), ("error", "недоступен")])
def test_telegram_failure_and_retry(bot_demo, scenario, text):
    bot, sent = bot_demo
    chat, _, callback = full_dialog(bot, sent)
    bot._alean_dialog.sessions[chat]["scenario"] = scenario
    callback("search")
    assert text in sent[-1][1]
    bot._alean_dialog.sessions[chat]["scenario"] = "success"
    callback("search")
    assert "вымышленный" in sent[-1][1]


def test_cancel_suppresses_late_live_result(monkeypatch):
    dialog = AleanDialog(); sent = []; send = lambda *a, **kw: sent.append(a)
    started = threading.Event(); release = threading.Event(); finished = threading.Event()
    original = search.search
    def fake(*args, **kwargs):
        started.set(); release.wait(3)
        try: return original(query())
        finally: finished.set()
    monkeypatch.setattr(search, "search", fake)
    dialog.start(1, send, mode="live")
    session = dialog.sessions[1]; session["query"] = query(); session["index"] = len(dialog.steps(session))
    dialog.run_search(1, session, send)
    assert started.wait(2)
    dialog.text(1, "/cancel", send); count = len(sent)
    release.set(); assert finished.wait(2)
    assert len(sent) == count


def test_demo_only_blocks_submission_and_other_dialog_callbacks(bot_demo, monkeypatch):
    bot, sent = bot_demo
    monkeypatch.setattr(bot, "ALEAN_DEMO_ONLY", True)
    bot.handle_completion(770001, "fictional", {})
    assert "отключены" in sent[-1][1]
    response = bot.app.test_client().post("/miniapp/submit", json={})
    assert response.status_code == 403


def test_other_lead_buttons_cannot_submit_during_readonly_search(bot_demo):
    bot, sent = bot_demo
    chat, _, _ = full_dialog(bot, sent)
    bot._alean_dialog.callback(chat, "c:confirm:fictional-old-lead", bot.send_message)
    assert "отправка заявок отключена" in sent[-1][1]


def test_demo_only_rejects_legacy_miniapp_transport(monkeypatch):
    import bot
    monkeypatch.setattr(bot, "ALEAN_DEMO_ONLY", True)
    with pytest.raises(bot.MiniAppValidationError, match="submissions disabled"):
        bot._accept_miniapp_trip(770001, {}, {})


def test_live_endpoint_keeps_signed_identity_out_of_supplier_query(bot_demo, monkeypatch):
    bot, _ = bot_demo
    enable(monkeypatch)
    monkeypatch.setattr(bot, "validate_init_data", lambda *a, **kw: {"id": 770001, "first_name": "Fixture"})
    def supplier(settings, session, params):
        assert not {"id", "chat_id", "phone", "first_name", "initData"}.intersection(params)
        return tv.SearchResult()
    monkeypatch.setattr(search.alean, "search_tours", supplier)
    response = bot.app.test_client().post("/miniapp/alean/search",
        json={"query": query(), "mode": "live", "initData": "fixture-only"})
    assert response.status_code == 200 and response.json["state"] == "empty"
    assert response.json["mode"] == "live"


def test_miniapp_readonly_endpoint_never_enters_lead_path(bot_demo):
    bot, _ = bot_demo
    client = bot.app.test_client()
    result = client.post("/miniapp/alean/search", json={"query": query(), "mode": "demo"})
    assert result.status_code == 200 and result.json["offers"]
    assert result.headers["Cache-Control"] == "no-store"
    assert client.post("/miniapp/alean/search", json={"query": query(), "mode": "live"}).status_code == 401
    assert client.post("/miniapp/alean/search", json={"query": {} }).status_code == 400
    assert client.post("/miniapp/alean/search", json={"query": query(), "scenario": "timeout"}).status_code == 503
    with client.get("/miniapp/alean/alean.html") as response:
        assert response.status_code == 200
    assert client.get("/miniapp/alean/secret.env").status_code == 404
