"""Unit tests for the TurBot VK webhook flow and helpers."""

import os
import json
import time
import tempfile

# Configure the VK bot before it is imported.
os.environ.setdefault("VK_ACCESS_TOKEN", "dummy-token")
os.environ.setdefault("VK_GROUP_ID", "999")
os.environ.setdefault("VK_CONFIRMATION", "confirm123")
os.environ.setdefault("VK_SECRET_KEY", "vk-test-secret")
os.environ.setdefault("TUTU_ENABLED", "false")
os.environ.setdefault("VK_DEMO_MODE", "false")
os.environ.setdefault("ADMIN_ID", "999")
os.environ.setdefault("DIALOG_TIMEOUT_HOURS", "0")
os.environ.setdefault("SYNC_COMPLETION", "true")  # run MDT/AI inline in tests
os.environ.setdefault("AI_MODE", "template")
os.environ.setdefault("CONSENT_MODE", "strict")  # classic consent in unit tests
# Присваивание, а не setdefault: test_bot.py импортируется раньше и уже задал
# DATABASE_PATH, из-за чего обе сюиты работали в одном файле. Миграция VK-бота
# при этом никогда не выполнялась целиком — таблицы успевал создать Telegram-бот,
# и опечатка в ALTER TABLE прожила до первого одиночного запуска этого файла.
os.environ["DATABASE_PATH"] = os.path.join(
    tempfile.gettempdir(), f"vk_turbot_test_{os.getpid()}.sqlite"
)

import vk_bot as bot

import pytest

# Фикстура ниже глушит send_message во всех тестах. Тестам про клавиатуру нужна
# настоящая: проверяется как раз то, что она подставляет клавиатуру по
# состоянию. Ссылка берётся до подмены.
_REAL_SEND_MESSAGE = bot.send_message


@pytest.fixture(autouse=True)
def clean_state(monkeypatch):
    bot.user_data.clear()
    bot.all_users.clear()
    with bot._db_cursor(commit=True) as cur:
        cur.execute("DELETE FROM sessions")
        cur.execute("DELETE FROM users")
        cur.execute("DELETE FROM leads")
    monkeypatch.setattr(bot, "send_message", lambda *a, **k: None)
    monkeypatch.setattr(bot, "send_typing", lambda *a, **k: None)
    monkeypatch.setattr(bot, "save_state", lambda: None)
    monkeypatch.setattr(bot, "get_user_name", lambda uid: f"TestUser{uid}")
    monkeypatch.setattr(bot, "_notify_admin", lambda *a, **k: None)
    monkeypatch.setattr(bot, "send_lead_to_mdt", lambda *a, **k: None)
    monkeypatch.setattr(bot._tutu, "mcp_call", lambda *a, **k: None)


@pytest.fixture
def client():
    return bot.app.test_client()


def _vk_message(user_id, text=None):
    """Build a minimal VK message_new event."""
    msg = {"peer_id": user_id, "from_id": user_id}
    if text is not None:
        msg["text"] = text
    return {
        "type": "message_new",
        "object": {"message": msg},
        "group_id": 999,
        "secret": bot.VK_SECRET_KEY,
    }


def _post(client, user_id, text=None):
    return client.post("/vk/webhook",
                       json=_vk_message(user_id, text),
                       content_type="application/json")


def test_validate_phone():
    assert bot.validate_phone("+79161234567") == (True, "+79161234567")
    assert bot.validate_phone("89161234567") == (True, "+79161234567")
    assert bot.validate_phone("9161234567") == (True, "+79161234567")
    assert bot.validate_phone("abc") == (False, None)


def test_validate_people():
    assert bot.validate_people("3") == (True, "3")
    assert bot.validate_people("5+") == (True, "5+")
    assert bot.validate_people("0") == (False, None)
    assert bot.validate_people("100") == (False, None)


def test_validate_budget():
    assert bot.validate_budget("60000") == (True, 60000)
    assert bot.validate_budget("60 000 руб") == (True, 60000)
    assert bot.validate_budget("0") == (False, None)


def test_health_endpoint(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["status"] == "ok"
    assert data["platform"] == "vk"
    assert data["revision"]
    assert "total_users" not in data
    assert "vk_group_id" not in data


def test_confirmation(client):
    resp = client.post("/vk/webhook",
                       json={"type": "confirmation", "group_id": 999,
                             "secret": bot.VK_SECRET_KEY},
                       content_type="application/json")
    assert resp.status_code == 200
    assert resp.get_data(as_text=True) == "confirm123"


def test_confirmation_does_not_require_a_vk_secret(client, monkeypatch):
    monkeypatch.setattr(bot, "VK_SECRET_KEY", "vk-test-secret")
    resp = client.post(
        "/vk/webhook",
        json={"type": "confirmation", "group_id": 999},
        content_type="application/json",
    )
    assert resp.status_code == 200
    assert resp.get_data(as_text=True) == "confirm123"


def test_vk_webhook_rejects_an_invalid_secret(client, monkeypatch):
    monkeypatch.setattr(bot, "VK_SECRET_KEY", "vk-test-secret")
    event = _vk_message(110, "Начать")
    event["secret"] = "not-the-vk-secret"
    resp = client.post("/vk/webhook", json=event, content_type="application/json")

    assert resp.status_code == 403
    assert 110 not in bot.user_data


def test_start_asks_for_consent(client):
    resp = _post(client, 111, "Начать")
    assert resp.status_code == 200
    assert bot.user_data[111]["state"] == bot.STATE_CONSENT
    assert not bot.has_consent(111)


def test_consent_accept_enters_dialog(client):
    _post(client, 111, "Начать")
    _post(client, 111, bot.CONSENT_YES_TEXT)
    assert bot.has_consent(111)
    assert bot.user_data[111]["state"] == bot.STATE_DESTINATION


def test_consent_decline_aborts(client):
    _post(client, 112, "Начать")
    _post(client, 112, bot.CONSENT_NO_TEXT)
    assert 112 not in bot.user_data
    assert not bot.has_consent(112)


def test_returning_user_skips_consent(client):
    _post(client, 113, "Начать")
    _post(client, 113, bot.CONSENT_YES_TEXT)
    _post(client, 113, "Отмена")
    _post(client, 113, "Начать")
    assert bot.user_data[113]["state"] == bot.STATE_DESTINATION


def test_dialog_completion(client):
    _post(client, 222, "Начать")
    _post(client, 222, bot.CONSENT_YES_TEXT)
    for text in ["Египет", "Москва", "15-22 июня", "2", "Без детей", "60000"]:
        _post(client, 222, text)
    assert bot.user_data[222]["state"] == bot.STATE_REVIEW
    _post(client, 222, bot.REVIEW_CONFIRM_TEXT)
    assert 222 not in bot.user_data
    with bot._db_cursor() as cur:
        cur.execute("SELECT phone, destination FROM leads WHERE chat_id = ?", (222,))
        row = cur.fetchone()
    assert row is not None
    assert "VK" in row[0]
    assert "Египет" in (row[1] or "")


def test_soft_mode_and_vk_contact(client, monkeypatch):
    monkeypatch.setattr(bot, "CONSENT_MODE", "soft")
    _post(client, 901, "Начать")
    assert bot.user_data[901]["state"] == bot.STATE_CONSENT
    _post(client, 901, bot.START_BUTTON_TEXT)
    assert bot.has_consent(901)
    assert bot.user_data[901]["state"] == bot.STATE_DESTINATION
    for text in [
        "Турция", "Москва", bot.DATE_PRESETS[0][0], bot.NIGHTS_PRESETS[0][0],
        "2", "Без детей", bot.BUDGET_PRESETS[1][0],
    ]:
        _post(client, 901, text)
    assert bot.user_data[901]["state"] == bot.STATE_REVIEW
    _post(client, 901, bot.REVIEW_CONFIRM_TEXT)
    assert 901 not in bot.user_data
    with bot._db_cursor() as cur:
        cur.execute("SELECT phone FROM leads WHERE chat_id = ?", (901,))
        row = cur.fetchone()
    assert row is not None
    assert "VK" in row[0]


def test_dates_and_budget_keyboards():
    d = json.loads(bot._dates_keyboard())
    labels = [b["action"]["label"] for row in d["buttons"] for b in row]
    assert bot.DATE_PRESETS[0][0] in labels
    assert bot.DATE_CUSTOM_LABEL in labels
    b = json.loads(bot._budget_keyboard())
    blabels = [btn["action"]["label"] for row in b["buttons"] for btn in row]
    assert bot.BUDGET_PRESETS[0][0] in blabels


def test_vk_budget_presets_are_strict_total_caps(client, monkeypatch):
    captured = []
    monkeypatch.setattr(bot, "send_message", lambda uid, text, **kwargs: captured.append(text))
    bot.user_data[958] = {"state": bot.STATE_BUDGET}

    bot._step_budget(958, bot.BUDGET_PRESETS[-1][0], {}, bot.user_data[958])

    assert bot.user_data[958]["budget_open_ended"] is False
    formatted = f"до {bot.BUDGET_PRESETS[-1][1]:,} ₽".replace(",", " ")
    assert any(formatted in text.lower() for text in captured)
    assert bot._budget_summary(bot.user_data[958]) == "до 300 000 ₽ на всю поездку"


def test_vk_budget_is_total_and_review_offers_clear_contact_choice(client):
    _post(client, 9571, "Начать")
    _post(client, 9571, bot.CONSENT_YES_TEXT)
    for text in ["Турция", "Москва", "15-22 сентября", "2", "0", "200000"]:
        _post(client, 9571, text)

    assert bot.user_data[9571]["budget_scope"] == "total"
    labels = [
        button["action"]["label"]
        for row in json.loads(bot._review_keyboard())["buttons"]
        for button in row
    ]
    assert bot.CONTACT_OTHER_TEXT in labels
    assert bot.CONTACT_PHONE_TEXT not in labels
    assert bot.CONTACT_MAX_TEXT not in labels


def test_vk_mobile_origin_label_stores_full_city(client):
    """The compact mobile label must not degrade the city saved in the lead."""
    labels = [label for label, _ in bot.ORIGIN_PRESETS]
    assert labels == ["Архангельск", "Москва", "Петербург", "Другой город"]
    keyboard_rows = json.loads(bot._origin_keyboard())["buttons"]
    assert [button["action"]["label"] for button in keyboard_rows[-2]] == ["Другой город"]
    assert keyboard_rows[-2][0]["color"] == "secondary"

    _post(client, 955, "Начать")
    _post(client, 955, bot.CONSENT_YES_TEXT)
    _post(client, 955, "Египет")
    _post(client, 955, "Петербург")

    assert bot.user_data[955]["origin"] == "Санкт-Петербург"
    assert bot.user_data[955]["state"] == bot.STATE_DATES


def test_vk_short_departure_window_asks_for_nights(client):
    _post(client, 9561, "Начать")
    _post(client, 9561, bot.CONSENT_YES_TEXT)
    for text in ["Турция", "Челябинск или Екатеринбург", "12-13 сентября"]:
        _post(client, 9561, text)

    assert bot.user_data[9561]["state"] == bot.STATE_NIGHTS
    assert bot.user_data[9561]["origin"] == "Челябинск / Екатеринбург"

    _post(client, 9561, "11-12")
    assert bot.user_data[9561]["state"] == bot.STATE_PEOPLE
    assert bot.user_data[9561]["nights"] == "11-12"
    assert bot.user_data[9561]["dates_are_trip"] is False


def test_vk_family_summary_does_not_double_count_teenager():
    info = {"people": "2", "kids_ages": [16, 9]}

    assert bot._party_text(info) == "2 взр. + дети: 9 и 16 лет"


def test_vk_searches_both_departure_cities(monkeypatch):
    calls = []
    user_id = 9562
    marker = "two-cities"
    bot.user_data[user_id] = {
        "state": bot.STATE_REVIEW,
        "_tour_search_marker": marker,
        "_tour_searching": True,
    }

    def fake_search(settings, session, info, **kwargs):
        calls.append(info["origin"])
        return bot._tourvisor.SearchResult(offers=[bot._tourvisor.TourOffer(
            hotel=f"Hotel {info['origin']}", category=4, region="Анталья",
            date="2030-09-12", nights=11, meal="AI", room="Family",
            operator="Operator", price=200000, departure=info["origin"],
        )])

    monkeypatch.setattr(bot._tourvisor, "search_tours", fake_search)
    monkeypatch.setattr(bot, "_send_tour_results_page", lambda *args: None)

    bot._tour_search_worker(user_id, marker, {"origin": "Челябинск / Екатеринбург"})

    assert calls == ["Челябинск", "Екатеринбург"]
    departures = {offer["departure"] for offer in bot.user_data[user_id]["_tour_offers"]}
    assert departures == {"Челябинск", "Екатеринбург"}


def test_vk_can_request_specific_hotel_and_explicitly_show_over_budget(monkeypatch):
    bot.user_data[9563] = {"state": bot.STATE_REVIEW}
    monkeypatch.setattr(bot, "send_message", lambda *args, **kwargs: None)

    bot._step_review(9563, bot.REVIEW_HOTEL_TEXT, {}, bot.user_data[9563])
    assert bot.user_data[9563]["state"] == bot.STATE_HOTEL
    bot._step_hotel(9563, "Belkon Hotel", {}, bot.user_data[9563])
    assert bot.user_data[9563]["hotel_query"] == "Belkon Hotel"

    called = []
    monkeypatch.setattr(
        bot, "_start_tour_search",
        lambda uid, info, **kwargs: called.append(kwargs),
    )
    bot._step_review(
        9563, bot.TOUR_SHOW_OVER_BUDGET_TEXT, {}, bot.user_data[9563],
    )
    assert called == [{"ignore_budget": True}]


def test_vk_review_allows_fixing_dates_and_budget(client):
    _post(client, 902, "Начать")
    _post(client, 902, bot.CONSENT_YES_TEXT)
    for text in ["Египет", "Москва", "15-22 сентября", "2", "0", "70000"]:
        _post(client, 902, text)
    assert bot.user_data[902]["state"] == bot.STATE_REVIEW

    _post(client, 902, bot.REVIEW_EDIT_DATES_TEXT)
    assert bot.user_data[902]["state"] == bot.STATE_DATES
    _post(client, 902, "20-27 сентября")
    for text in ["2", "0", "70000"]:
        _post(client, 902, text)
    assert bot.user_data[902]["state"] == bot.STATE_REVIEW

    _post(client, 902, bot.REVIEW_EDIT_BUDGET_TEXT)
    assert bot.user_data[902]["state"] == bot.STATE_BUDGET


def test_vk_review_can_show_tourvisor_results(monkeypatch):
    captured = []
    user_id = 956
    bot.user_data[user_id] = {
        "state": bot.STATE_REVIEW,
        "destination": "Турция",
        "origin": "Москва",
        "dates": "15-22 сентября 2030",
        "people": "2",
        "kids_ages": [],
        "budget": 100000,
    }
    result = bot._tourvisor.SearchResult(offers=[bot._tourvisor.TourOffer(
        hotel="Test Hotel", category=4, region="Сиде", date="2030-09-15",
        nights=7, meal="Всё включено", room="Standard", operator="Test Operator",
        price=190000,
    )])
    monkeypatch.setattr(bot, "TOURVISOR_ENABLED", True)
    monkeypatch.setattr(bot, "TOURVISOR_TOKEN", "test-token")
    monkeypatch.setattr(bot._tourvisor, "search_tours", lambda *args, **kwargs: result)
    monkeypatch.setattr(bot, "send_tour_carousel", lambda *args, **kwargs: False)
    monkeypatch.setattr(bot, "send_message", lambda uid, text, **kwargs: captured.append(text))

    bot._start_tour_search(user_id, bot.user_data[user_id])

    assert any("Ищу актуальные туры" in text for text in captured)
    assert any("Test Hotel" in text for text in captured)
    assert not bot.user_data[user_id].get("_tour_searching")


def test_vk_search_replaces_review_keyboard_with_waiting_status(monkeypatch):
    edits = []
    user_id = 958
    bot.user_data[user_id] = {
        "state": bot.STATE_REVIEW,
        "destination": "Турция",
        "origin": "Москва",
        "dates": "15-22 сентября 2030",
        "people": "2",
        "kids_ages": [],
        "budget": 200000,
        "_review_message_id": 777,
    }
    monkeypatch.setattr(bot, "TOURVISOR_ENABLED", True)
    monkeypatch.setattr(bot, "TOURVISOR_TOKEN", "test-token")
    monkeypatch.setattr(bot, "SYNC_COMPLETION", False)
    monkeypatch.setattr(bot, "send_typing", lambda *args: None)
    monkeypatch.setattr(
        bot,
        "edit_message",
        lambda uid, mid, text, keyboard=None: edits.append((uid, mid, text, keyboard)) or True,
    )
    started = []

    class FakeThread:
        def __init__(self, *, target, args, **kwargs):
            started.append((target, args))

        def start(self):
            return None

    monkeypatch.setattr(bot.threading, "Thread", FakeThread)

    bot._start_tour_search(user_id, bot.user_data[user_id])

    assert edits[0][1] == 777
    assert "Ищу актуальные туры" in edits[0][2]
    labels = [
        button["action"]["label"]
        for row in json.loads(edits[0][3])["buttons"]
        for button in row
    ]
    assert labels == [bot.CANCEL_BUTTON_TEXT]
    assert len(started) == 2


def test_vk_tourvisor_native_carousel_contains_selectable_tour_ids(monkeypatch):
    captured = []
    offers = [{
        "hotel": "Test Hotel", "category": 4, "region": "Сиде",
        "nights": 7, "meal": "Всё включено", "price": 190000,
        "currency": "RUB", "fuel_charge": 5000, "tour_id": "tour-77",
        "picture_url": "https://example.test/hotel.jpg",
    }]
    monkeypatch.setattr(bot, "_upload_vk_message_photo", lambda *args: "-999_123")
    monkeypatch.setattr(
        bot, "_vk_api",
        lambda method, **params: captured.append((method, params)) or 1,
    )

    assert bot.send_tour_carousel(956, offers, 0) is True

    method, params = captured[-1]
    assert method == "messages.send"
    template = json.loads(params["template"])
    card = template["elements"][0]
    assert template["type"] == "carousel"
    assert card["photo_id"] == "-999_123"
    assert card["buttons"][0]["action"]["label"] == "Выбрать №1"
    payload = json.loads(card["buttons"][0]["action"]["payload"])
    assert payload["tour_id"] == "tour-77"


def test_vk_more_tours_pages_existing_results_without_new_search(monkeypatch):
    pages = []
    bot.user_data[959] = {
        "state": bot.STATE_REVIEW,
        "_tour_page": 0,
        "_tour_offers": [
            {"hotel": f"Hotel {i}", "category": 4, "region": "Сиде",
             "date": "2030-09-15", "nights": 7, "meal": "AI", "room": "Std",
             "operator": "Operator", "price": 100000 + i, "currency": "RUB",
             "fuel_charge": 0, "tour_id": str(i), "picture_url": ""}
            for i in range(1, 5)
        ],
    }
    captured = []
    monkeypatch.setattr(bot, "send_message", lambda uid, text, **kwargs: captured.append(text))

    bot._step_review(959, bot.TOUR_MORE_BUTTON_TEXT, {}, bot.user_data[959])

    assert any("Hotel 4" in text for text in captured)
    assert bot.user_data[959]["_tour_page"] == 1


def test_vk_selected_tour_is_persisted_with_lead(monkeypatch):
    selected = {
        "hotel": "Selected Hotel", "category": 5, "region": "Анталья",
        "date": "2030-09-15", "nights": 7, "meal": "Всё включено",
        "room": "Family", "operator": "Operator", "price": 250000,
        "currency": "RUB", "fuel_charge": 0, "tour_id": "offer-42",
        "picture_url": "",
    }
    bot.user_data[960] = {
        "state": bot.STATE_REVIEW,
        "_tour_offers": [selected],
        "destination": "Турция",
    }
    monkeypatch.setattr(bot, "send_message", lambda *args, **kwargs: None)

    bot._select_tour(960, 1)
    bot.save_lead(960, bot.user_data[960], "VK", "Test User")

    assert bot.user_data[960]["selected_tour"]["tour_id"] == "offer-42"
    with bot._db_cursor() as cur:
        cur.execute("SELECT selected_tour FROM leads WHERE chat_id = ?", (960,))
        stored = json.loads(cur.fetchone()[0])
    assert stored["hotel"] == "Selected Hotel"
    assert stored["tour_id"] == "offer-42"
    assert "ID предложения: offer-42" in bot._selected_tour_summary(bot.user_data[960])
    labels = [
        button["action"]["label"]
        for row in json.loads(bot._selected_tour_keyboard())["buttons"]
        for button in row
    ]
    assert bot.TOUR_SEND_SELECTED_TEXT in labels
    assert bot.CONTACT_OTHER_TEXT in labels
    assert bot.BACK_BUTTON_TEXT in labels


def test_vk_selected_tour_can_choose_phone_without_extra_contact_step(monkeypatch):
    captured = []
    user_id = 9601
    bot.user_data[user_id] = {"state": bot.STATE_REVIEW, "selected_tour": {"hotel": "Test"}}
    monkeypatch.setattr(
        bot,
        "send_message",
        lambda uid, text, **kwargs: captured.append((text, kwargs.get("keyboard"))),
    )

    bot._step_review(user_id, bot.CONTACT_PHONE_TEXT, {}, bot.user_data[user_id])

    assert bot.user_data[user_id]["state"] == bot.STATE_PHONE
    assert bot.user_data[user_id]["contact_method"] == "phone"
    assert "номер телефона" in captured[-1][0]


def test_vk_tour_filters_reuse_existing_results(monkeypatch):
    offers = [
        {"hotel": "Budget", "category": 3, "meal": "RO", "price": 90000, "fuel_charge": 0},
        {"hotel": "Premium", "category": 5, "meal": "BB", "price": 180000, "fuel_charge": 0},
        {"hotel": "All Inclusive", "category": 4, "meal": "AI", "price": 150000, "fuel_charge": 0},
    ]
    bot.user_data[961] = {
        "state": bot.STATE_REVIEW,
        "_tour_offers": list(offers),
        "_tour_offers_base": list(offers),
    }
    shown = []
    monkeypatch.setattr(
        bot,
        "_send_tour_results_page",
        lambda uid, page: shown.append([o["hotel"] for o in bot.user_data[uid]["_tour_offers"]]),
    )
    monkeypatch.setattr(bot, "send_message", lambda *args, **kwargs: None)

    bot._apply_tour_filter(961, "better")
    bot._apply_tour_filter(961, "all_inclusive")

    assert shown[0] == ["Premium", "All Inclusive", "Budget"]
    assert shown[1] == ["All Inclusive"]


def test_vk_fallback_sends_text_and_buttons_together(monkeypatch):
    sent = []
    bot.user_data[962] = {
        "state": bot.STATE_REVIEW,
        "_tour_offers": [{
            "hotel": "Hotel", "category": 4, "region": "Сиде",
            "date": "2030-09-15", "nights": 7, "meal": "RO", "room": "Std",
            "operator": "Operator", "price": 100000, "currency": "RUB",
            "fuel_charge": 0, "tour_id": "1", "picture_url": "",
        }],
    }
    monkeypatch.setattr(bot, "send_tour_carousel", lambda *args, **kwargs: False)
    monkeypatch.setattr(
        bot, "send_message",
        lambda uid, text, **kwargs: sent.append((text, kwargs.get("keyboard"))),
    )

    bot._send_tour_results_page(962, 0)

    assert len(sent) == 1
    assert "Hotel" in sent[0][0]
    assert sent[0][1]
    labels = [
        button["action"]["label"]
        for row in json.loads(sent[0][1])["buttons"]
        for button in row
    ]
    assert "№1" in labels


def test_vk_discards_tour_result_after_user_leaves_review(monkeypatch):
    captured = []
    user_id = 957
    marker = "search-1"
    bot.user_data[user_id] = {
        "state": bot.STATE_CONTACT,
        "_tour_searching": True,
        "_tour_search_marker": marker,
    }
    monkeypatch.setattr(
        bot._tourvisor,
        "search_tours",
        lambda *args, **kwargs: bot._tourvisor.SearchResult(offers=[
            bot._tourvisor.TourOffer(
                hotel="Late Hotel", category=4, region="", date="2030-09-15",
                nights=7, meal="", room="", operator="", price=100000,
            )
        ]),
    )
    monkeypatch.setattr(bot, "send_message", lambda *args, **kwargs: captured.append(args))

    bot._tour_search_worker(user_id, marker, dict(bot.user_data[user_id]))

    assert captured == []
    assert not bot.user_data[user_id].get("_tour_searching")


def test_vk_undecided_destination_marks_consultation(client):
    _post(client, 903, "Начать")
    _post(client, 903, bot.CONSENT_YES_TEXT)
    _post(client, 903, bot.DIRECTION_UNDECIDED_LABEL)

    assert bot.user_data[903]["state"] == bot.STATE_ORIGIN
    assert bot.user_data[903]["destination"] == bot.UNDECIDED_DESTINATION
    assert bot.user_data[903]["needs_consultation"] is True


def test_vk_consultation_flag_is_persisted():
    bot.set_session(904, {
        "state": bot.STATE_ORIGIN,
        "destination": bot.UNDECIDED_DESTINATION,
        "needs_consultation": True,
    })

    saved = bot.get_session(904)
    assert saved is not None
    assert saved["needs_consultation"] == 1


def test_back_button(client):
    _post(client, 333, "Начать")
    _post(client, 333, bot.CONSENT_YES_TEXT)
    _post(client, 333, "Египет")
    assert bot.user_data[333]["state"] == bot.STATE_ORIGIN
    _post(client, 333, bot.BACK_BUTTON_TEXT)
    assert bot.user_data[333]["state"] == bot.STATE_DESTINATION


def test_cancel_button(client):
    _post(client, 444, "Начать")
    _post(client, 444, bot.CONSENT_YES_TEXT)
    assert 444 in bot.user_data
    _post(client, 444, bot.CANCEL_BUTTON_TEXT)
    assert 444 not in bot.user_data


def test_delete_command(client):
    _post(client, 555, "Начать")
    _post(client, 555, bot.CONSENT_YES_TEXT)
    assert bot.has_consent(555)
    _post(client, 555, "Удалить")
    assert 555 not in bot.user_data
    assert 555 not in bot.all_users
    assert not bot.has_consent(555)


def test_privacy_command(client):
    resp = _post(client, 666, "Политика")
    assert resp.status_code == 200


def test_template_selection():
    text = bot._template_selection("Турция", "15-22 июня", "2", "60000")
    assert "Турция" in text


def test_keyboard_format():
    kb = bot._dest_keyboard()
    data = json.loads(kb) if isinstance(kb, str) else kb
    assert "buttons" in data
    assert len(data["buttons"]) > 0
    assert data["inline"] is False


def _vk_consent(client, uid):
    _post(client, uid, "Начать")
    _post(client, uid, bot.CONSENT_YES_TEXT)


def test_vk_funnel_asks_for_origin(client):
    """VK must collect the departure city too, or Tutu cannot price a flight."""
    _vk_consent(client, 901)
    _post(client, 901, "Египет")
    assert bot.user_data[901]["state"] == bot.STATE_ORIGIN
    _post(client, 901, "Москва")
    assert bot.user_data[901]["state"] == bot.STATE_DATES
    assert bot.user_data[901]["origin"] == "Москва"


def test_vk_origin_persisted_with_lead(client):
    _vk_consent(client, 902)
    for text in ["Турция", "Санкт-Петербург", "1-7 августа", "2", "Без детей", "70000"]:
        _post(client, 902, text)
    _post(client, 902, bot.REVIEW_CONFIRM_TEXT)
    with bot._db_cursor() as cur:
        cur.execute("SELECT origin FROM leads WHERE chat_id = ?", (902,))
        assert cur.fetchone()[0] == "Санкт-Петербург"


def test_vk_demo_mode_masks_phone(client, monkeypatch):
    """VK_DEMO_MODE is independent of DEMO_MODE: one bot can be a showcase
    while the other takes real enquiries for the agency."""
    monkeypatch.setattr(bot, "DEMO_MODE", True)
    _vk_consent(client, 903)
    for text in ["Турция", "Москва", "1-7 августа", "2", "Без детей", "70000"]:
        _post(client, 903, text)
    _post(client, 903, bot.CONTACT_OTHER_TEXT)
    _post(client, 903, "+79161234567")
    with bot._db_cursor() as cur:
        cur.execute("SELECT phone FROM leads WHERE chat_id = ?", (903,))
        stored = cur.fetchone()[0]
    assert stored == "+7916***4567"
    assert "1234567" not in stored


def test_vk_real_mode_keeps_phone(client, monkeypatch):
    monkeypatch.setattr(bot, "DEMO_MODE", False)
    _vk_consent(client, 904)
    for text in ["Турция", "Москва", "1-7 августа", "2", "Без детей", "70000"]:
        _post(client, 904, text)
    _post(client, 904, bot.CONTACT_OTHER_TEXT)
    _post(client, 904, "+79161234567")
    with bot._db_cursor() as cur:
        cur.execute("SELECT phone FROM leads WHERE chat_id = ?", (904,))
        assert cur.fetchone()[0] == "+79161234567"


def test_vk_concurrent_completion_creates_one_lead(monkeypatch):
    import threading

    user_id = 9041
    bot.user_data[user_id] = {
        "state": bot.STATE_CONTACT,
        "destination": "Турция",
        "origin": "Москва",
        "dates": "1-7 августа",
        "people": "2",
        "kids": 0,
        "infants": 0,
        "budget": 70000,
        "updated_at": int(time.time()),
    }
    monkeypatch.setattr(bot, "send_message", lambda *args, **kwargs: None)
    monkeypatch.setattr(bot, "_notify_admin", lambda *args, **kwargs: None)
    monkeypatch.setattr(bot, "_post_completion_side_effects", lambda *args, **kwargs: None)

    real_save = bot.save_lead

    def slow_save(*args, **kwargs):
        time.sleep(0.05)
        return real_save(*args, **kwargs)

    monkeypatch.setattr(bot, "save_lead", slow_save)
    barrier = threading.Barrier(2)

    def complete():
        barrier.wait()
        bot.handle_completion(user_id, "+79161234567", {"_user_name": "Гонка"})

    workers = [threading.Thread(target=complete) for _ in range(2)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=10)

    with bot._db_cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM leads WHERE chat_id = ?", (user_id,))
        assert cur.fetchone()[0] == 1


def test_vk_tutu_switch_is_independent(monkeypatch):
    """One .env, two audiences: VK can drop live prices while Telegram keeps
    them. Without a separate flag, turning Tutu off for the agency's real
    enquiries would also blank the portfolio showcase."""
    import importlib
    monkeypatch.setenv("TUTU_ENABLED", "true")
    monkeypatch.setenv("VK_TUTU_ENABLED", "false")
    reloaded = importlib.reload(bot)
    try:
        assert reloaded.TUTU_ENABLED is False
    finally:
        monkeypatch.delenv("VK_TUTU_ENABLED", raising=False)
        importlib.reload(bot)


def test_vk_asks_for_child_ages(client):
    """Parity with Telegram — and VK is the side the agency actually uses.

    The manager's complaint came in through VK: "дети 2 человека, а возрасты
    какие?". A step that exists only in the Telegram bot would not have
    answered her.
    """
    _post(client, 905, "Начать")
    _post(client, 905, bot.CONSENT_YES_TEXT)
    for text in ["Египет", "Москва", "15-22 сентября", "2"]:
        _post(client, 905, text)
    assert bot.user_data[905]["state"] == bot.STATE_KIDS_AGES

    _post(client, 905, "до года, 7")
    assert bot.user_data[905]["state"] == bot.STATE_BUDGET
    assert bot.user_data[905]["kids_ages"] == [0, 7]
    # Bands are derived, never asked twice.
    assert bot.user_data[905]["infants"] == 1
    assert bot.user_data[905]["kids"] == 1


def test_vk_child_age_prompt_has_no_upper_age_limit(monkeypatch):
    messages = []
    monkeypatch.setattr(bot, "send_message", lambda _uid, text, **_kwargs: messages.append(text))

    bot._ask_people(956)
    bot._ask_kids_ages(956)

    prompt = "\n".join(messages)
    assert "до 12" not in prompt
    assert "Возраст детей" in prompt
    assert "5, 9" in prompt


def test_vk_no_kids_button_skips_age_entry(client):
    _post(client, 907, "Начать")
    _post(client, 907, bot.CONSENT_YES_TEXT)
    for text in ["Египет", "Москва", "15-22 сентября", "2"]:
        _post(client, 907, text)

    _post(client, 907, bot.NO_KIDS_BUTTON_TEXT)

    assert bot.user_data[907]["state"] == bot.STATE_BUDGET
    assert bot.user_data[907]["kids_ages"] == []
    assert bot.user_data[907]["kids"] == 0
    assert bot.user_data[907]["infants"] == 0


def test_vk_child_age_keyboard_has_no_kids_shortcut():
    keyboard = json.loads(bot._kids_ages_keyboard())
    labels = [
        button["action"]["label"]
        for row in keyboard["buttons"]
        for button in row
    ]
    assert bot.NO_KIDS_BUTTON_TEXT in labels
    assert bot.BACK_BUTTON_TEXT in labels
    assert bot.CANCEL_BUTTON_TEXT in labels


def test_vk_child_ages_show_budget_keyboard_once(client, monkeypatch):
    captured = []
    monkeypatch.setattr(bot, "send_message", _REAL_SEND_MESSAGE)
    monkeypatch.setattr(bot, "_vk_api",
                        lambda method, **p: captured.append(p) or {"response": 1})
    for text in ["Начать", bot.CONSENT_YES_TEXT, "Египет", "Москва",
                 "15-22 сентября", "2"]:
        _post(client, 908, text)
    captured.clear()

    _post(client, 908, bot.NO_KIDS_BUTTON_TEXT)

    assert len(captured) == 1
    assert "Записал: 2 взр." in captured[0]["message"]
    labels = [
        button["action"]["label"]
        for row in json.loads(captured[0]["keyboard"])["buttons"]
        for button in row
    ]
    assert bot.BUDGET_PRESETS[0][0] in labels


def test_vk_stores_child_ages_with_the_lead(client):
    _post(client, 906, "Начать")
    _post(client, 906, bot.CONSENT_YES_TEXT)
    for text in ["Египет", "Москва", "15-22 сентября", "2", "6", "70000"]:
        _post(client, 906, text)
    _post(client, 906, bot.REVIEW_CONFIRM_TEXT)
    with bot._db_cursor() as cur:
        cur.execute("SELECT kids_ages FROM leads WHERE chat_id = ?", (906,))
        assert cur.fetchone()[0] == "6"


def _reach_contact(client, uid):
    """Пройти воронку до шага «как связаться»."""
    _post(client, uid, "Начать")
    _post(client, uid, bot.CONSENT_YES_TEXT)
    for text in ["Египет", "Москва", "15-22 сентября", "2",
                 "0", "70000"]:
        _post(client, uid, text)
    assert bot.user_data[uid]["state"] == bot.STATE_REVIEW
    _post(client, uid, bot.CONTACT_OTHER_TEXT)
    assert bot.user_data[uid]["state"] == bot.STATE_CONTACT


def test_vk_offers_max_instead_of_telegram(client):
    """Клиент пришёл из VK — Telegram ему предлагать незачем."""
    labels = [
        btn["action"]["label"]
        for row in json.loads(bot._contact_keyboard())["buttons"]
        for btn in row
    ]
    assert bot.CONTACT_MAX_TEXT in labels
    assert not any("Telegram" in l for l in labels)


def test_max_accepts_a_profile_link(client):
    _reach_contact(client, 907)
    _post(client, 907, bot.CONTACT_MAX_TEXT)
    assert bot.user_data[907]["state"] == bot.STATE_MAX
    _post(client, 907, "вот я: max.ru/u/AbC-123")
    with bot._db_cursor() as cur:
        cur.execute("SELECT phone FROM leads WHERE chat_id = ?", (907,))
        assert cur.fetchone()[0] == "MAX https://max.ru/u/AbC-123"


def test_max_accepts_a_phone(client):
    """Номер — основной способ найти человека в MAX, а не запасной."""
    _reach_contact(client, 908)
    _post(client, 908, bot.CONTACT_MAX_TEXT)
    _post(client, 908, "89161234567")
    with bot._db_cursor() as cur:
        cur.execute("SELECT phone FROM leads WHERE chat_id = ?", (908,))
        assert cur.fetchone()[0] == "MAX +79161234567"


def test_max_rejects_an_at_handle_with_a_reason(client, monkeypatch):
    """У личных профилей MAX нет @никнеймов — принять его значит потерять клиента.

    Скопированный с Telegram шаг молча сохранил бы «@ivan», и менеджер не
    смогла бы никого найти.
    """
    sent = []
    monkeypatch.setattr(bot, "send_message",
                        lambda uid, text, **k: sent.append(text))
    _reach_contact(client, 909)
    _post(client, 909, bot.CONTACT_MAX_TEXT)
    _post(client, 909, "@ivanov")
    assert bot.user_data[909]["state"] == bot.STATE_MAX, "шаг не должен закрываться"
    assert "никнеймов" in sent[-1]
    with bot._db_cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM leads WHERE chat_id = ?", (909,))
        assert cur.fetchone()[0] == 0, "заявка не должна уйти с ненаходимым контактом"


def test_keyboards_are_standard_and_fit_the_limit():
    """Клавиатура отображается под полем ввода (inline=False), чтобы не засорять чат."""
    builders = [n for n in dir(bot) if n.endswith("_keyboard") and n != "_keyboard"]
    assert builders, "клавиатуры не найдены — тест перестал что-либо проверять"
    for name in builders:
        raw = getattr(bot, name)()
        k = json.loads(raw)
        rows = k["buttons"]
        total = sum(len(r) for r in rows)
        if name == "_hide_keyboard":
            assert k["inline"] is False and total == 0
            continue
        assert k["inline"] is False, f"{name} не должна быть inline"
        assert total <= 40, f"{name}: {total} кнопок, лимит — 40"
        assert len(rows) <= 10, f"{name}: {len(rows)} рядов, лимит — 10"
        assert all(len(r) <= 5 for r in rows), f"{name}: больше 5 кнопок в ряду"


def test_old_client_gets_a_plain_keyboard_instead_of_none():
    """VK клиенты получают стандартную клавиатуру."""
    bot._NO_INLINE.discard(555)
    kb = bot._contact_keyboard()
    assert json.loads(bot._downgrade_if_needed(555, kb))["inline"] is False

    bot._remember_client_capabilities(555, {"object": {"client_info": {"inline_keyboard": False}}})
    assert json.loads(bot._downgrade_if_needed(555, kb))["inline"] is False
    assert json.loads(bot._downgrade_if_needed(555, kb))["buttons"]

    bot._remember_client_capabilities(555, {"object": {"client_info": {"inline_keyboard": True}}})
    assert json.loads(bot._downgrade_if_needed(555, kb))["inline"] is False


def test_missing_client_info_is_treated_as_modern():
    """Неизвестный клиент — современный. Иначе одно пропущенное поле в событии
    лишило бы кнопок вообще всех."""
    bot._NO_INLINE.discard(556)
    bot._remember_client_capabilities(556, {"object": {"message": {"text": "hi"}}})
    assert 556 not in bot._NO_INLINE


def _kb_of(sent):
    """Клавиатура последнего отправленного сообщения."""
    return sent[-1][1]


def test_every_reply_carries_a_way_forward(client, monkeypatch):
    """Кнопки то были, то нет: клавиатуру передавали руками, и два десятка
    сообщений уходили без неё. Теперь она берётся из состояния."""
    captured = []
    monkeypatch.setattr(bot, "send_message", _REAL_SEND_MESSAGE)
    monkeypatch.setattr(bot, "_vk_api",
                        lambda method, **p: captured.append(p) or {"response": 1})

    for text in ["Начать", bot.CONSENT_YES_TEXT, "Египет", "Москва",
                 "15-22 сентября", "непонятный текст"]:
        _post(client, 950, text)

    assert captured, "ни одного вызова messages.send"
    without = [p["message"][:45] for p in captured if not p.get("keyboard")]
    assert not without, f"сообщения без кнопок: {without}"


def test_cancel_shows_the_button_it_names(client, monkeypatch):
    """Сообщение говорило «Когда будете готовы — Начать» и тем же вызовом
    прятало клавиатуру."""
    captured = []
    monkeypatch.setattr(bot, "send_message", _REAL_SEND_MESSAGE)
    monkeypatch.setattr(bot, "_vk_api",
                        lambda method, **p: captured.append(p) or {"response": 1})
    _post(client, 951, "Начать")
    _post(client, 951, bot.CONSENT_YES_TEXT)
    captured.clear()
    _post(client, 951, "Отмена")

    final = captured[-1]
    assert "Начать" in final["message"]
    # json.dumps экранирует кириллицу, поэтому сверяем разобранные подписи.
    labels = [
        b["action"]["label"]
        for row in json.loads(final["keyboard"])["buttons"] for b in row
    ]
    assert bot.START_BUTTON_TEXT in labels, "кнопка названа, но не показана"


def test_the_word_buttons_brings_the_step_back(client, monkeypatch):
    """Человек, у которого «пропали кнопки», ищет команду, а не догадку."""
    captured = []
    monkeypatch.setattr(bot, "send_message", _REAL_SEND_MESSAGE)
    monkeypatch.setattr(bot, "_vk_api",
                        lambda method, **p: captured.append(p) or {"response": 1})
    for text in ["Начать", bot.CONSENT_YES_TEXT, "Египет"]:
        _post(client, 952, text)
    state_before = bot.user_data[952]["state"]
    captured.clear()

    _post(client, 952, "кнопки")
    assert bot.user_data[952]["state"] == state_before, "шаг не должен меняться"
    assert captured[-1].get("keyboard"), "кнопки не вернулись"


def test_completion_followup_does_not_bring_back_start_button(monkeypatch):
    """После завершения заявки follow-up не должен снова приклеивать soft-start."""
    captured = []
    user_id = 953
    bot.user_data[user_id] = {
        "state": bot.STATE_MAX,
        "destination": "Египет",
        "origin": "Архангельск",
        "dates": "даты гибкие",
        "adults": 4,
        "children_ages": [5, 14],
        "budget": 80000,
        "contact": "VK",
    }

    monkeypatch.setattr(bot, "send_message", _REAL_SEND_MESSAGE)
    monkeypatch.setattr(bot, "_vk_api",
                        lambda method, **p: captured.append((method, p)) or {"response": 1})
    monkeypatch.setattr(bot, "_notify_admin", lambda *a, **k: None)
    monkeypatch.setattr(bot, "send_lead_to_mdt", lambda *a, **k: None)
    monkeypatch.setattr(bot, "generate_ai_selection",
                        lambda *a, **k: "✅ Заявка у менеджера.\n\nМенеджер подберёт варианты.")

    bot.handle_completion(user_id, "VK (чат id 31771632)", {"_user_name": "Роман Неклюдов"})

    user_messages = [p for method, p in captured if method == "messages.send" and p["user_id"] == user_id]
    assert len(user_messages) >= 2, "ожидали подтверждение заявки и follow-up клиенту"
    for payload in user_messages[-2:]:
        kb = json.loads(payload["keyboard"])
        labels = [
            b["action"]["label"]
            for row in kb.get("buttons", [])
            for b in row
        ]
        assert bot.START_BUTTON_TEXT not in labels, "soft-start вернулся после завершения заявки"


def test_completed_lead_does_not_interrupt_manager_dialog(client, monkeypatch):
    """Normal replies after a lead must stay silent for the human manager."""
    captured = []
    user_id = 954
    bot.set_consent(user_id)
    bot.save_lead(user_id, {"destination": "Египет"}, "VK (чат id 954)", "Тест")
    monkeypatch.setattr(bot, "send_message", _REAL_SEND_MESSAGE)
    monkeypatch.setattr(bot, "_vk_api",
                        lambda method, **p: captured.append(p) or {"response": 1})

    _post(client, user_id, "1")

    assert captured == []

    _post(client, user_id, "Начать")
    assert bot.user_data[user_id]["state"] == bot.STATE_DESTINATION


def test_vk_party_preset_two_adults_skips_kids_step(client):
    """Выбор «2 взрослых» сразу переходит к бюджету без лишнего вопроса про детей."""
    _vk_consent(client, 970)
    for text in ["Турция", "Архангельск", "15-22 сентября"]:
        _post(client, 970, text)
    assert bot.user_data[970]["state"] == bot.STATE_PEOPLE

    _post(client, 970, bot.PARTY_PRESET_2_ADULTS)
    assert bot.user_data[970]["state"] == bot.STATE_BUDGET
    assert bot.user_data[970]["people"] == "2"
    assert bot.user_data[970]["kids_ages"] == []
    assert bot.user_data[970]["kids"] == 0


def test_vk_party_preset_one_adult_skips_kids_step(client):
    """Выбор «1 взрослый» сразу переходит к бюджету."""
    _vk_consent(client, 971)
    for text in ["Египет", "Москва", "1-8 октября"]:
        _post(client, 971, text)

    _post(client, 971, bot.PARTY_PRESET_1_ADULT)
    assert bot.user_data[971]["state"] == bot.STATE_BUDGET
    assert bot.user_data[971]["people"] == "1"
    assert bot.user_data[971]["kids_ages"] == []


def test_vk_party_preset_family_with_kids_asks_age(client):
    """Выбор «2 взр. + 1 реб.» переходит к вопросу о возрасте ребёнка."""
    _vk_consent(client, 972)
    for text in ["ОАЭ", "Москва", "15-22 ноября"]:
        _post(client, 972, text)

    _post(client, 972, bot.PARTY_PRESET_2_PLUS_1)
    assert bot.user_data[972]["state"] == bot.STATE_KIDS_AGES
    assert bot.user_data[972]["people"] == "2"

    _post(client, 972, "6")
    assert bot.user_data[972]["state"] == bot.STATE_BUDGET
    assert bot.user_data[972]["kids_ages"] == [6]


def test_vk_hot_tours_button_shows_hot_deals(client):
    """Кнопка «Горящие туры» сразу выдает варианты горящих туров."""
    _vk_consent(client, 973)
    _post(client, 973, bot.DEST_HOT_TOURS_LABEL)
    assert bot.user_data[973]["state"] == bot.STATE_REVIEW
    assert bot.user_data[973]["_tour_offers"]
    assert len(bot.user_data[973]["_tour_offers"]) > 0


def test_vk_direct_flights_button_shows_destinations(client, monkeypatch):
    """Кнопка «Прямые вылеты» выдает список направлений из города вылета."""
    captured = []
    monkeypatch.setattr(bot, "send_message", lambda uid, text, **kwargs: captured.append(text))
    _vk_consent(client, 974)
    _post(client, 974, bot.DEST_DIRECT_FLIGHTS_LABEL)
    assert any("Прямые чартерные рейсы" in text for text in captured)

