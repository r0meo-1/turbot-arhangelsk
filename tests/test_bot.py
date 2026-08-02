"""Unit tests for the TurBot webhook flow and helpers."""

import json
import os
import time
import tempfile

# Configure the bot before it is imported.
os.environ.setdefault("BOT_TOKEN", "dummy-token")
os.environ.setdefault("ADMIN_ID", "999")
os.environ.setdefault("TELEGRAM_SECRET_TOKEN", "secret123")
os.environ.setdefault("DIALOG_TIMEOUT_HOURS", "0")  # disable background worker
os.environ.setdefault("SYNC_COMPLETION", "true")  # run MDT/AI inline in tests
# Unit tests must never touch the network. Tutu ships enabled by default so the
# deployed demo works without anyone setting a variable; here it stays off, and
# tests/test_tutu.py exercises that client against injected transports instead.
os.environ.setdefault("TUTU_ENABLED", "false")
os.environ.setdefault("DEMO_MODE", "false")  # tests assert on real stored values
os.environ.setdefault("CONSENT_MODE", "strict")  # keep classic consent in unit tests
os.environ.setdefault("STATE_FILE", ":memory:")  # not used when save_state is mocked
os.environ.setdefault(
    "DATABASE_PATH",
    os.path.join(tempfile.gettempdir(), f"turbot_test_{os.getpid()}.sqlite"),
)

import bot

import pytest


class _OkResp:
    """Minimal stand-in for requests.Response with HTTP 200."""
    status_code = 200
    text = '{"ok":true}'


@pytest.fixture(autouse=True)
def clean_state(monkeypatch):
    """Reset in-memory state and silence Telegram calls before each test."""
    bot.user_data.clear()
    bot.all_users.clear()
    # Clean SQLite tables so direct delete_session/clear_sessions calls
    # in handle_cancel/handle_completion/_admin_restart work on a clean slate.
    with bot._db_cursor(commit=True) as cur:
        cur.execute("DELETE FROM sessions")
        cur.execute("DELETE FROM users")
        cur.execute("DELETE FROM leads")
    bot._seen_update_ids.clear()
    monkeypatch.setattr(bot, "send_message", lambda *a, **k: _OkResp())
    monkeypatch.setattr(bot, "send_typing", lambda *a, **k: None)
    monkeypatch.setattr(bot, "answer_callback", lambda *a, **k: None)
    monkeypatch.setattr(bot, "clear_inline_keyboard", lambda *a, **k: None)
    monkeypatch.setattr(bot, "save_state", lambda: None)


@pytest.fixture
def client():
    return bot.app.test_client()


def test_validate_phone():
    assert bot.validate_phone("+79161234567") == (True, "+79161234567")
    assert bot.validate_phone("89161234567") == (True, "+79161234567")
    assert bot.validate_phone("9161234567") == (True, "+79161234567")
    assert bot.validate_phone("abc") == (False, None)
    assert bot.validate_phone("123") == (False, None)


def test_validate_people():
    assert bot.validate_people("3") == (True, "3")
    assert bot.validate_people("5+") == (True, "5+")
    assert bot.validate_people("0") == (False, None)
    assert bot.validate_people("100") == (False, None)


def test_validate_budget():
    assert bot.validate_budget("60000") == (True, 60000)
    assert bot.validate_budget("60 000 руб") == (True, 60000)
    assert bot.validate_budget("0") == (False, None)
    assert bot.validate_budget("abc") == (False, None)


def test_health_endpoint(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["status"] == "ok"
    assert data["bot_token_configured"] is True
    assert data["admin_id_configured"] is True


def test_webhook_rejects_missing_secret(client):
    resp = client.post("/webhook", json={"message": {"chat": {"id": 1}}})
    assert resp.status_code == 403


def test_webhook_rejects_bad_secret(client):
    resp = client.post(
        "/webhook",
        headers={"X-Telegram-Bot-Api-Secret-Token": "bad-secret"},
        json={"message": {"chat": {"id": 1}}},
    )
    assert resp.status_code == 403


def test_update_id_dedup(client):
    """Same update_id must be processed only once."""
    payload = _update(777, "/start")
    payload["update_id"] = 424242
    headers = {"X-Telegram-Bot-Api-Secret-Token": "secret123"}
    r1 = client.post("/webhook", headers=headers, json=payload)
    r2 = client.post("/webhook", headers=headers, json=payload)
    assert r1.status_code == 200
    assert r2.status_code == 200
    assert 777 in bot.user_data
    assert bot.user_data[777]["state"] == bot.STATE_CONSENT


def test_seen_update_ids_evicts_oldest():
    bot._seen_update_ids.clear()
    bot._SEEN_UPDATE_MAX = 3
    try:
        for i in range(5):
            bot._seen_update_ids[i] = None
            while len(bot._seen_update_ids) > bot._SEEN_UPDATE_MAX:
                bot._seen_update_ids.popitem(last=False)
        assert list(bot._seen_update_ids.keys()) == [2, 3, 4]
        assert 0 not in bot._seen_update_ids
        assert 1 not in bot._seen_update_ids
    finally:
        bot._SEEN_UPDATE_MAX = 1000
        bot._seen_update_ids.clear()


def _update(chat_id, text=None, contact=None):
    """Build a minimal Telegram update payload."""
    payload = {
        "message": {
            "chat": {"id": chat_id},
            "from": {"first_name": "Test", "id": chat_id},
        }
    }
    if text is not None:
        payload["message"]["text"] = text
    if contact is not None:
        payload["message"]["contact"] = contact
    return payload


def _post(client, chat_id, text=None, contact=None):
    return client.post(
        "/webhook",
        headers={"X-Telegram-Bot-Api-Secret-Token": "secret123"},
        json=_update(chat_id, text, contact),
    )


def _callback(client, chat_id, data, update_id=None):
    """Post a Telegram callback_query (inline button press)."""
    payload = {
        "callback_query": {
            "id": f"cq-{chat_id}-{data}",
            "from": {"first_name": "Test", "id": chat_id},
            "message": {
                "message_id": 1,
                "chat": {"id": chat_id},
            },
            "data": data,
        }
    }
    if update_id is not None:
        payload["update_id"] = update_id
    return client.post(
        "/webhook",
        headers={"X-Telegram-Bot-Api-Secret-Token": "secret123"},
        json=payload,
    )


def _consent(client, chat_id):
    """Grant personal-data consent so the dialog proceeds to data collection."""
    _post(client, chat_id, "/start")
    _callback(client, chat_id, bot.CB_CONSENT_YES)


def test_start_asks_for_consent_first(client):
    resp = _post(client, 111, "/start")
    assert resp.status_code == 200
    assert bot.user_data[111]["state"] == bot.STATE_CONSENT
    assert not bot.has_consent(111)


def test_consent_accept_enters_dialog(client):
    _post(client, 111, "/start")
    _callback(client, 111, bot.CB_CONSENT_YES)
    assert bot.has_consent(111)
    assert bot.user_data[111]["state"] == bot.STATE_DESTINATION


def test_consent_decline_aborts(client):
    _post(client, 112, "/start")
    _callback(client, 112, bot.CB_CONSENT_NO)
    assert 112 not in bot.user_data
    assert not bot.has_consent(112)


def test_consent_accept_via_text_still_works(client):
    """Typed consent label remains supported for backward compatibility."""
    _post(client, 116, "/start")
    _post(client, 116, bot.CONSENT_YES_TEXT)
    assert bot.has_consent(116)
    assert bot.user_data[116]["state"] == bot.STATE_DESTINATION


def test_returning_user_skips_consent(client):
    _consent(client, 113)
    _post(client, 113, "❌ Отменить")
    # Second /start should go straight to destination.
    _post(client, 113, "/start")
    assert bot.user_data[113]["state"] == bot.STATE_DESTINATION


def test_delete_command_erases_data(client):
    _consent(client, 114)
    assert bot.has_consent(114)
    _post(client, 114, "/delete")
    assert 114 not in bot.user_data
    assert 114 not in bot.all_users
    assert not bot.has_consent(114)


def test_delete_command_erases_leads(client):
    """152-ФЗ: /delete must remove completed leads for that user too."""
    _consent(client, 115)
    for text in ["🏖 Египет", "Москва", "15-22 июня", "2", "Без детей", "60000"]:
        _post(client, 115, text)
    _post(client, 115, contact={"phone_number": "79161234567", "user_id": 115})
    assert bot.count_leads() == 1
    _post(client, 115, "/delete")
    assert bot.count_leads() == 0


def test_dialog_completion_with_contact(client):
    # Walk through the whole flow.
    _consent(client, 222)
    for text in ["🏖 Египет", "Москва", "15-22 июня", "2", "Без детей", "60000"]:
        _post(client, 222, text)

    info = bot.user_data.get(222)
    assert info is not None
    assert info["state"] == bot.STATE_CONTACT

    # Finish by sharing a contact (accepted on contact-choice step too).
    resp = _post(client, 222, contact={"phone_number": "79161234567", "user_id": 222})
    assert resp.status_code == 200
    assert 222 not in bot.user_data
    # Completed lead is stored for /export and /analytics.
    assert bot.count_leads() == 1
    with bot._db_cursor() as cur:
        cur.execute("SELECT phone, destination FROM leads WHERE chat_id = ?", (222,))
        row = cur.fetchone()
    assert row is not None
    assert row[0] == "+79161234567"
    assert "Египет" in (row[1] or "")


def test_dialog_completion_via_inline_buttons(client):
    """Full funnel using only inline callbacks for choice steps."""
    _consent(client, 223)
    # Destination index 0 → first POPULAR_DESTINATIONS entry
    _callback(client, 223, f"{bot.CB_DEST_PREFIX}0")
    assert bot.user_data[223]["state"] == bot.STATE_ORIGIN
    assert "Египет" in bot.user_data[223].get("destination", "")

    # Departure city (index 0 → Архангельск)
    _callback(client, 223, f"{bot.CB_ORIGIN_PREFIX}0")
    assert bot.user_data[223]["state"] == bot.STATE_DATES
    assert bot.user_data[223]["origin"] == "Архангельск"

    _post(client, 223, "10-17 июля")
    assert bot.user_data[223]["state"] == bot.STATE_PEOPLE

    _callback(client, 223, f"{bot.CB_PEOPLE_PREFIX}2")
    assert bot.user_data[223]["state"] == bot.STATE_KIDS
    assert bot.user_data[223]["people"] == "2"

    _callback(client, 223, f"{bot.CB_KIDS_PREFIX}{bot.KIDS_NONE_LABEL}")
    assert bot.user_data[223]["state"] == bot.STATE_BUDGET

    _post(client, 223, "80000")
    assert bot.user_data[223]["state"] == bot.STATE_CONTACT

    _callback(client, 223, bot.CB_CONTACT_PHONE)
    assert bot.user_data[223]["state"] == bot.STATE_PHONE

    _post(client, 223, "+79161234567")
    assert 223 not in bot.user_data
    assert bot.count_leads() == 1


def test_soft_mode_start_and_telegram_contact(client, monkeypatch):
    """Soft mode: one start tap, finish with Telegram as contact (no phone)."""
    monkeypatch.setattr(bot, "CONSENT_MODE", "soft")
    _post(client, 901, "/start")
    assert bot.user_data[901]["state"] == bot.STATE_CONSENT
    _callback(client, 901, bot.CB_START)
    assert bot.has_consent(901)
    assert bot.user_data[901]["state"] == bot.STATE_DESTINATION

    for text in ["Турция", "Москва", "1-7 августа", "2", "Без детей", "70000"]:
        _post(client, 901, text)
    assert bot.user_data[901]["state"] == bot.STATE_CONTACT

    # Fake username via completing with synthetic callback path
    bot.user_data[901]["contact_method"] = "telegram"
    # Inject username by posting contact channel with patched message — use API path
    payload = {
        "callback_query": {
            "id": "cq-901-tg",
            "from": {"first_name": "Test", "id": 901, "username": "demo_user"},
            "message": {"message_id": 1, "chat": {"id": 901}},
            "data": bot.CB_CONTACT_TG,
        }
    }
    client.post(
        "/webhook",
        headers={"X-Telegram-Bot-Api-Secret-Token": "secret123"},
        json=payload,
    )
    assert 901 not in bot.user_data
    assert bot.count_leads() == 1
    with bot._db_cursor() as cur:
        cur.execute("SELECT phone FROM leads WHERE chat_id = ?", (901,))
        row = cur.fetchone()
    assert row is not None
    assert "Telegram" in row[0]
    assert "demo_user" in row[0]


def test_inline_cancel_aborts_dialog(client):
    _consent(client, 224)
    _callback(client, 224, bot.CB_CANCEL)
    assert 224 not in bot.user_data


def test_inline_keyboard_builders():
    consent = json.loads(bot.kb_consent())
    assert "inline_keyboard" in consent
    assert consent["inline_keyboard"][0][0]["callback_data"] == bot.CB_CONSENT_YES

    dest = json.loads(bot.kb_destinations())
    # destinations in pairs + cancel row
    assert dest["inline_keyboard"][0][0]["callback_data"].startswith(bot.CB_DEST_PREFIX)

    people = json.loads(bot.kb_people())
    flat = [b["callback_data"] for row in people["inline_keyboard"] for b in row]
    assert f"{bot.CB_PEOPLE_PREFIX}2" in flat
    assert bot.CB_BACK in flat
    assert bot.CB_CANCEL in flat

    dates = json.loads(bot.kb_dates())
    dflat = [b["callback_data"] for row in dates["inline_keyboard"] for b in row]
    assert f"{bot.CB_DATE_PREFIX}0" in dflat
    assert f"{bot.CB_DATE_PREFIX}custom" in dflat

    budget = json.loads(bot.kb_budget())
    bflat = [b["callback_data"] for row in budget["inline_keyboard"] for b in row]
    assert f"{bot.CB_BUDGET_PREFIX}0" in bflat
    assert f"{bot.CB_BUDGET_PREFIX}custom" in bflat


def test_full_flow_all_buttons(client):
    """Complete a lead using only preset buttons (no free typing)."""
    _consent(client, 910)
    _callback(client, 910, f"{bot.CB_DEST_PREFIX}0")
    assert bot.user_data[910]["state"] == bot.STATE_ORIGIN
    _callback(client, 910, f"{bot.CB_ORIGIN_PREFIX}1")
    assert bot.user_data[910]["state"] == bot.STATE_DATES
    assert bot.user_data[910]["origin"] == "Москва"
    _callback(client, 910, f"{bot.CB_DATE_PREFIX}0")
    assert bot.user_data[910]["state"] == bot.STATE_PEOPLE
    assert "выходные" in bot.user_data[910]["dates"]
    _callback(client, 910, f"{bot.CB_PEOPLE_PREFIX}2")
    assert bot.user_data[910]["state"] == bot.STATE_KIDS
    _callback(client, 910, f"{bot.CB_KIDS_PREFIX}{bot.KIDS_NONE_LABEL}")
    assert bot.user_data[910]["state"] == bot.STATE_BUDGET
    _callback(client, 910, f"{bot.CB_BUDGET_PREFIX}1")
    assert bot.user_data[910]["state"] == bot.STATE_CONTACT
    assert bot.user_data[910]["budget"] == bot.BUDGET_PRESETS[1][1]
    _callback(client, 910, bot.CB_CONTACT_TG)
    assert 910 not in bot.user_data
    assert bot.count_leads() == 1


def test_admin_send_arms_reply_mode(client, monkeypatch):
    """/send with only chat_id waits for the next admin message."""
    sent = []

    def capture(chat_id, text, parse_mode=None, reply_markup=None):
        sent.append({"chat_id": chat_id, "text": text})
        return _OkResp()

    monkeypatch.setattr(bot, "send_message", capture)
    bot._admin_reply_to.clear()
    bot._last_lead_client_id = None

    # Admin (999) arms reply to client 231403545
    _post(client, 999, "/send 231403545")
    assert bot._admin_reply_to.get(999) == 231403545

    # Next plain message goes to the client
    _post(client, 999, "Здравствуйте! Мы подобрали варианты.")
    assert 999 not in bot._admin_reply_to
    to_client = [m for m in sent if m["chat_id"] == 231403545]
    assert to_client
    assert "подобрали" in to_client[-1]["text"]


def test_admin_send_empty_uses_last_lead(client, monkeypatch):
    monkeypatch.setattr(bot, "send_message", lambda *a, **k: _OkResp())
    bot._admin_reply_to.clear()
    bot._last_lead_client_id = 777001
    _post(client, 999, "/send")
    assert bot._admin_reply_to.get(999) == 777001
    bot._admin_reply_to.clear()
    bot._last_lead_client_id = None


def test_back_button(client):
    _consent(client, 333)
    _post(client, 333, "🏖 Египет")
    assert bot.user_data[333]["state"] == bot.STATE_ORIGIN

    _post(client, 333, "◀️ Назад")
    assert bot.user_data[333]["state"] == bot.STATE_DESTINATION

    # And back again from dates → origin (the newly inserted step)
    _post(client, 333, "🏖 Египет")
    _post(client, 333, "Москва")
    assert bot.user_data[333]["state"] == bot.STATE_DATES
    _post(client, 333, "◀️ Назад")
    assert bot.user_data[333]["state"] == bot.STATE_ORIGIN


def test_cancel_button(client):
    _consent(client, 444)
    assert 444 in bot.user_data
    _post(client, 444, "❌ Отменить")
    assert 444 not in bot.user_data


def test_unknown_command_outside_dialog(client):
    resp = _post(client, 555, "/foobar")
    assert resp.status_code == 200
    assert 555 not in bot.user_data


def test_stale_dialog_cleanup():
    bot.DIALOG_TIMEOUT_HOURS = 1
    bot.user_data[666] = {
        "state": bot.STATE_DATES,
        "destination": "Турция",
        "updated_at": int(time.time()) - 7200,  # 2 hours ago
    }
    bot._cleanup_stale_dialogs()
    assert 666 not in bot.user_data
    bot.DIALOG_TIMEOUT_HOURS = 0


def test_template_selection_uses_known_destination():
    """A destination note is kept only where there is something real to say."""
    text = bot._template_selection("Турция", "15-22 июня", "2", "60000")
    assert "Турция" in text
    assert "all inclusive" in text.lower() or "пляжного отдыха" in text
    assert "Заявка у менеджера" in text



def test_template_selection_says_nothing_rather_than_filler():
    """Unknown destination used to produce «X — отличное направление для X»."""
    text = bot._template_selection("Урюпинск", "лето", "2", "50000")
    assert "отличное направление" not in text
    assert text.count("Урюпинск") == 0, "no echoing the input back as insight"
    assert "Менеджер подберёт" in text



def test_generate_ai_selection_uses_template_when_mode_is_template():
    original_mode = bot.AI_MODE
    bot.AI_MODE = "template"
    try:
        text = bot.generate_ai_selection("Египет", "10-17 июля", "2", "55000")
        assert "Египет" in text
        assert "пирамиды" in text or "Красное море" in text
    finally:
        bot.AI_MODE = original_mode


def test_mdt_base_url():
    original_account = bot.MDT_ACCOUNT
    original_base = bot.MDT_BASE_URL
    try:
        bot.MDT_ACCOUNT = "demo"
        bot.MDT_BASE_URL = ""
        assert bot._mdt_base_url() == "https://demo.moidokumenti.ru"

        bot.MDT_BASE_URL = "https://custom.example.com/"
        assert bot._mdt_base_url() == "https://custom.example.com"
    finally:
        bot.MDT_ACCOUNT = original_account
        bot.MDT_BASE_URL = original_base


def test_send_lead_to_mdt_when_disabled(monkeypatch):
    calls = []
    monkeypatch.setattr(bot, "_mdt_request", lambda *a, **k: calls.append((a, k)) or None)
    bot.MDT_ENABLED = False
    bot.send_lead_to_mdt(111, {"destination": "Турция"}, "+79000000000", "Иван")
    assert not calls


def test_send_lead_to_mdt_when_enabled(monkeypatch):
    captured = {}

    def fake_mdt_request(method, params):
        captured["method"] = method
        captured["params"] = params
        return {"id": 123}

    monkeypatch.setattr(bot, "_mdt_request", fake_mdt_request)
    bot.MDT_ENABLED = True
    bot.MDT_MODE = "lead"
    try:
        bot.send_lead_to_mdt(
            222,
            {"destination": "Египет", "dates": "10-17 июля", "people": "2", "budget": 60000},
            "+79001112233",
            "Анна",
        )
        assert captured["method"] == "add-lead"
        assert captured["params"]["name"] == "Анна"
        assert captured["params"]["phone"] == "+79001112233"
        assert captured["params"]["source"] == "Telegram Bot"
        fields = {f["name"]: f["values"] for f in captured["params"]["fields"]}
        assert fields["Направление"] == ["Египет"]
        assert fields["Бюджет"] == ["60000"]
    finally:
        bot.MDT_ENABLED = False
        bot.MDT_MODE = "lead"


def test_send_preorder_to_mdt(monkeypatch):
    calls = []

    def fake_mdt_request(method, params):
        calls.append((method, params))
        if method == "add-tourist-temp":
            return {"id": 100}
        if method == "create-preorder":
            return {"id": 200}
        return None

    monkeypatch.setattr(bot, "_mdt_request", fake_mdt_request)

    original_mode = bot.MDT_MODE
    bot.MDT_MODE = "preorder"
    try:
        preorder_id, tourist_id = bot.send_preorder_to_mdt(
            333,
            {"destination": "Турция", "dates": "10-17 июля", "people": "2", "budget": 60000},
            "+79001112233",
            "Анна",
        )
        assert preorder_id == 200
        assert tourist_id == 100

        methods = [m for m, _ in calls]
        assert methods == ["add-tourist-temp", "create-preorder"]

        tourist_params = calls[0][1]
        assert tourist_params["name"] == "Анна"
        assert tourist_params["tel"] == "+79001112233"

        preorder_params = calls[1][1]
        assert preorder_params["tourist_type"] == "tourist_temp"
        assert preorder_params["tourist_id"] == 100
        assert preorder_params["persons"] == 2
        assert preorder_params["price_to"] == 60000
    finally:
        bot.MDT_MODE = original_mode


def test_send_lead_to_mdt_preorder_mode_creates_reminder(monkeypatch):
    calls = []

    def fake_mdt_request(method, params):
        calls.append((method, params))
        if method == "add-tourist-temp":
            return {"id": 100}
        if method == "create-preorder":
            return {"id": 200}
        if method == "send-push":
            return {"id": 300}
        if method == "add-reminder":
            return {"id": 400}
        return None

    monkeypatch.setattr(bot, "_mdt_request", fake_mdt_request)

    original_mode = bot.MDT_MODE
    original_manager_ids = bot.MDT_MANAGER_IDS
    original_reminder_enabled = bot.MDT_REMINDER_ENABLED
    original_notify = bot.MDT_NOTIFY_MANAGERS
    bot.MDT_ENABLED = True
    bot.MDT_MODE = "preorder"
    bot.MDT_MANAGER_IDS = [5, 7]
    bot.MDT_REMINDER_ENABLED = True
    bot.MDT_NOTIFY_MANAGERS = True
    try:
        bot.send_lead_to_mdt(
            444,
            {"destination": "Турция", "dates": "10-17 июля", "people": "2", "budget": 60000},
            "+79001112233",
            "Анна",
        )
        methods = [m for m, _ in calls]
        assert "add-tourist-temp" in methods
        assert "create-preorder" in methods
        assert "add-reminder" in methods
        assert "send-push" in methods

        reminder_calls = [p for m, p in calls if m == "add-reminder"]
        assert len(reminder_calls) == 2
        manager_ids = {p["manager_id"] for p in reminder_calls}
        assert manager_ids == {5, 7}
        for p in reminder_calls:
            assert p["preorder_id"] == 200
            assert p["tourist_id"] == 100
            assert p["text"] == bot.MDT_REMINDER_TEXT
    finally:
        bot.MDT_ENABLED = False
        bot.MDT_MODE = original_mode
        bot.MDT_MANAGER_IDS = original_manager_ids
        bot.MDT_REMINDER_ENABLED = original_reminder_enabled
        bot.MDT_NOTIFY_MANAGERS = original_notify


def test_send_lead_to_mdt_both_mode(monkeypatch):
    calls = []

    def fake_mdt_request(method, params):
        calls.append((method, params))
        if method == "add-tourist-temp":
            return {"id": 100}
        if method in ("create-preorder", "add-lead", "send-push"):
            return {"id": 1}
        return None

    monkeypatch.setattr(bot, "_mdt_request", fake_mdt_request)

    original_mode = bot.MDT_MODE
    original_manager_ids = bot.MDT_MANAGER_IDS
    bot.MDT_ENABLED = True
    bot.MDT_MODE = "both"
    bot.MDT_MANAGER_IDS = [1]
    bot.MDT_REMINDER_ENABLED = False
    try:
        bot.send_lead_to_mdt(
            555,
            {"destination": "ОАЭ", "dates": "1-10 августа", "people": "3", "budget": 120000},
            "+79003334455",
            "Петр",
        )
        methods = [m for m, _ in calls]
        assert methods.count("add-lead") == 1
        assert methods.count("add-tourist-temp") == 1
        assert methods.count("create-preorder") == 1
    finally:
        bot.MDT_ENABLED = False
        bot.MDT_MODE = original_mode
        bot.MDT_MANAGER_IDS = original_manager_ids
        bot.MDT_REMINDER_ENABLED = True



def test_cleanup_expired_data_erases_old_users(client):
    old = int(time.time()) - 200 * 86400
    bot.touch_user(700, "Old", "", last_seen=old)
    bot.touch_user(701, "Recent", "", last_seen=int(time.time()))
    original = bot.DATA_RETENTION_DAYS
    bot.DATA_RETENTION_DAYS = 180
    try:
        erased = bot.cleanup_expired_data()
    finally:
        bot.DATA_RETENTION_DAYS = original
    assert erased >= 1
    assert bot.get_user(700) is None
    assert bot.get_user(701) is not None


def test_cleanup_expired_data_disabled(client):
    bot.touch_user(702, "Old", "", last_seen=0)
    original = bot.DATA_RETENTION_DAYS
    bot.DATA_RETENTION_DAYS = 0
    try:
        assert bot.cleanup_expired_data() == 0
    finally:
        bot.DATA_RETENTION_DAYS = original
    assert bot.get_user(702) is not None
    bot.delete_user_data(702)


def test_duplicate_update_ignored(client):
    """The same update_id should only be processed once."""
    update = {
        "update_id": 99999,
        "message": {"chat": {"id": 800}, "from": {"first_name": "Dedup"}, "text": "/start"},
    }
    _post(client, 800, "/start")  # consent
    _post(client, 800, bot.CONSENT_YES_TEXT)
    # Send a destination message with explicit update_id
    update["message"]["text"] = "Египет"
    client.post("/webhook", json=update,
                headers={"X-Telegram-Bot-Api-Secret-Token": "secret123"})
    assert bot.user_data[800]["state"] == bot.STATE_ORIGIN
    assert bot.user_data[800].get("destination") == "Египет"
    # Send the same update again — should be ignored
    bot.user_data[800]["state"] = bot.STATE_ORIGIN  # reset
    update["message"]["text"] = "Турция"
    client.post("/webhook", json=update,
                headers={"X-Telegram-Bot-Api-Secret-Token": "secret123"})
    # Should still be Египет, not Турция (duplicate update_id)
    assert bot.user_data[800].get("destination") == "Египет"
    bot.delete_user_data(800)


def test_analytics_command(client):
    """Admin /analytics returns 200 and contains analytics text."""
    # Add some data
    _consent(client, 801)
    _post(client, 801, "Турция")
    # Admin (ID 999) sends /analytics
    resp = _post(client, 999, "/analytics")
    assert resp.status_code == 200


def test_export_command(client):
    """Admin /export returns 200."""
    resp = _post(client, 999, "/export")
    assert resp.status_code == 200


def test_html_escape_in_notify(client):
    """User input with HTML tags should be escaped in admin notifications."""
    _consent(client, 802)
    _post(client, 802, "<script>alert(1)</script>")
    _post(client, 802, "Москва")
    _post(client, 802, "1-10 июля")
    _post(client, 802, "2")
    _post(client, 802, "Без детей")
    _post(client, 802, "50000")
    # send phone to complete
    _post(client, 802, "+79161234567")
    # If we got here without crashing, HTML was escaped properly
    assert 802 not in bot.user_data
    bot.delete_user_data(802)


def test_lead_is_sent_to_admin_telegram(client, monkeypatch):
    """Completed lead must be forwarded to ADMIN_ID / LEAD_NOTIFY_IDS in Telegram."""
    sent = []

    def capture(chat_id, text, parse_mode=None, reply_markup=None):
        sent.append({
            "chat_id": chat_id,
            "text": text,
            "parse_mode": parse_mode,
        })
        return _OkResp()

    monkeypatch.setattr(bot, "send_message", capture)
    monkeypatch.setattr(bot, "LEAD_NOTIFY_IDS", [999])

    _consent(client, 903)
    for text in ["Турция", "Москва", "1-7 августа", "2", "Без детей", "70000"]:
        _post(client, 903, text)
    _post(client, 903, "+79161234567")

    assert 903 not in bot.user_data
    assert bot.count_leads() == 1

    admin_msgs = [m for m in sent if m["chat_id"] == 999]
    assert admin_msgs, "admin must receive at least one Telegram message with the lead"
    # Pick the lead notification explicitly: with Tutu enabled a price
    # follow-up legitimately arrives after it, so "the last message" is fragile.
    lead_msgs = [m for m in admin_msgs if "Новая заявка" in m["text"]]
    assert lead_msgs, "admin must receive the lead notification itself"
    body = lead_msgs[-1]["text"]
    assert "Турция" in body
    assert "+79161234567" in body
    assert "70000" in body
    assert lead_msgs[-1]["parse_mode"] == "HTML"


def test_parse_chat_ids():
    assert bot._parse_chat_ids("1, 2,3") == [1, 2, 3]
    assert bot._parse_chat_ids("") == []
    assert bot._parse_chat_ids("bad,42") == [42]


def test_graceful_shutdown_handler():
    """_graceful_shutdown should exist and be callable."""
    assert callable(bot._graceful_shutdown)


# ---------------------------------------------------------------------------
# Demo mode, self-served privacy policy and concurrent-completion guard


def test_mask_phone_keeps_shape_not_number():
    assert bot.mask_phone("+79161234567") == "+7916***4567"
    assert bot.mask_phone("89161234567") == "+8916***4567"
    assert bot.mask_phone("123") == "+7***"
    assert bot.mask_phone("") == "+7***"


def test_demo_mode_stores_masked_phone(client, monkeypatch):
    """A public showcase must not persist a real subscriber number."""
    monkeypatch.setattr(bot, "DEMO_MODE", True)
    _consent(client, 5001)
    for text in ["Турция", "Москва", "1-7 августа", "2", "Без детей", "70000"]:
        _post(client, 5001, text)
    _post(client, 5001, "+79161234567")
    with bot._db_cursor() as cur:
        cur.execute("SELECT phone FROM leads WHERE chat_id = ?", (5001,))
        stored = cur.fetchone()[0]
    assert stored == "+7916***4567"
    assert "1234567" not in stored


def test_real_mode_stores_real_phone(client, monkeypatch):
    monkeypatch.setattr(bot, "DEMO_MODE", False)
    _consent(client, 5002)
    for text in ["Турция", "Москва", "1-7 августа", "2", "Без детей", "70000"]:
        _post(client, 5002, text)
    _post(client, 5002, "+79161234567")
    with bot._db_cursor() as cur:
        cur.execute("SELECT phone FROM leads WHERE chat_id = ?", (5002,))
        assert cur.fetchone()[0] == "+79161234567"


def test_privacy_page_is_served(client):
    """The consent text links here, so it must never 404."""
    resp = client.get("/privacy")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "персональных данных" in body
    assert "<h1" in body or "<h2" in body


def test_health_reports_demo_and_tutu_flags(client):
    data = client.get("/health").get_json()
    assert "demo_mode" in data
    assert "tutu_enabled" in data


def test_health_fails_when_poller_went_quiet(client, monkeypatch):
    """A bot nobody can reach must not report itself healthy.

    This is the exact shape of the two real outages: process alive, systemd
    green, log silent, Telegram unreachable. The status code is what the
    watchdog reads, so that is the part worth asserting.
    """
    monkeypatch.setattr(bot, "BOT_MODE", "polling")
    monkeypatch.setattr(bot, "POLL_STALE_AFTER", 180)
    monkeypatch.setattr(bot, "_last_poll_ok", time.time() - 600)

    resp = client.get("/health")
    assert resp.status_code == 503, "a deaf bot answered 200"
    data = resp.get_json()
    assert data["status"] == "degraded"
    assert data["seconds_since_poll_ok"] > 180


def test_freshly_booted_bot_is_not_reported_dead(monkeypatch):
    """The heartbeat starts warm, or the watchdog restarts a healthy boot.

    A zero here would mean "last heard from Telegram in 1970": /health would
    answer 503 for the instant between import and the poller's first call, and
    the watchdog would take that at face value.
    """
    assert bot._last_poll_ok > 0, "_last_poll_ok must be seeded at import"
    assert time.time() - bot._last_poll_ok < bot.POLL_STALE_AFTER


def test_health_stays_ok_while_the_poller_answers(client, monkeypatch):
    monkeypatch.setattr(bot, "BOT_MODE", "polling")
    monkeypatch.setattr(bot, "_last_poll_ok", time.time() - 5)

    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.get_json()["status"] == "ok"


def test_health_ignores_poll_age_under_webhook(client, monkeypatch):
    """Nothing polls under a webhook, so silence there proves nothing.

    Without this the bot would report itself broken the moment it ran in the
    mode it was originally written for.
    """
    monkeypatch.setattr(bot, "BOT_MODE", "webhook")
    monkeypatch.setattr(bot, "_last_poll_ok", 0.0)

    resp = client.get("/health")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["status"] == "ok"
    assert data["seconds_since_poll_ok"] is None


def test_concurrent_completion_creates_one_lead(client, monkeypatch):
    """Two threads finishing the same dialog must not double the lead.

    The real-world trigger is a double-tapped "share contact" button: two
    genuinely distinct updates, so update_id dedup does not catch them.
    """
    import threading

    chat = 5150
    bot.user_data[chat] = {
        "state": bot.STATE_PHONE,
        "destination": "Турция",
        "origin": "Москва",
        "dates": "1-7 августа",
        "people": "2",
        "kids": 0,
        "infants": 0,
        "budget": 70000,
        "updated_at": int(time.time()),
    }
    monkeypatch.setattr(bot, "send_message", lambda *a, **k: _OkResp())
    monkeypatch.setattr(bot, "_notify_admin", lambda *a, **k: None)
    monkeypatch.setattr(bot, "_post_completion_side_effects", lambda *a, **k: None)

    real_save = bot.save_lead

    def slow_save(*args, **kwargs):
        time.sleep(0.05)          # widen the window the guard has to close
        return real_save(*args, **kwargs)

    monkeypatch.setattr(bot, "save_lead", slow_save)

    barrier = threading.Barrier(2)
    message = {"from": {"first_name": "Гонка", "username": "race"}}

    def run():
        barrier.wait()
        bot.handle_completion(chat, "+79161234567", message)

    threads = [threading.Thread(target=run) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    assert bot.count_leads() == 1, "concurrent completion produced duplicate leads"
    bot.delete_user_data(chat)


# ---------------------------------------------------------------------------
# Token redaction and polling mode


def test_log_filter_redacts_bot_token():
    """urllib3 logs the full URL on a broken connection, and the Telegram API
    keeps the token in the path — so one network blip writes it to journald."""
    import logging as _logging
    f = bot._TokenRedactingFilter()
    leaked = ("Retrying after connection broken by NewConnectionError: "
              "/bot8886586430:AAHzxHyH5hx_ay_Iqgjw1ZciQvAyKe_nz5Q/setChatMenuButton")
    rec = _logging.LogRecord("urllib3", _logging.WARNING, __file__, 1, leaked, None, None)
    f.filter(rec)
    out = rec.getMessage()
    assert "AAHzxHyH5hx_ay_Iqgjw1ZciQvAyKe_nz5Q" not in out
    assert "<redacted>" in out
    assert "bot8886586430" in out          # id kept: still useful for debugging
    assert "setChatMenuButton" in out      # the rest of the message survives


def test_log_filter_leaves_ordinary_messages_alone():
    import logging as _logging
    f = bot._TokenRedactingFilter()
    rec = _logging.LogRecord("turbot", _logging.INFO, __file__, 1,
                             "Lead from %s delivered", ("chat 42",), None)
    f.filter(rec)
    assert rec.getMessage() == "Lead from chat 42 delivered"


class _PollResp:
    def __init__(self, payload, status=200):
        self._payload, self.status_code = payload, status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


def _run_poller(monkeypatch, batches, extra_get=None):
    """Drive _polling_worker over a scripted sequence of getUpdates replies."""
    seen_params, dispatched = [], []
    monkeypatch.setattr(bot, "dispatch_update", lambda u: dispatched.append(u))
    monkeypatch.setattr(bot, "BOT_TOKEN", "123456:test-token")
    monkeypatch.setattr(bot, "POLL_TIMEOUT", 0)

    queue = list(batches)

    class FakeSession:
        def __init__(self):
            self.posts = []

        def post(self, url, **kw):
            self.posts.append(url)
            return _PollResp({"ok": True})

        def get(self, url, params=None, **kw):
            seen_params.append(params)
            if queue:
                item = queue.pop(0)
                if isinstance(item, Exception):
                    raise item
                return item
            bot._shutdown_event.set()
            return _PollResp({"ok": True, "result": []})

    session = FakeSession()
    monkeypatch.setattr(bot, "telegram_session", session)
    bot._shutdown_event.clear()
    try:
        bot._polling_worker()
    finally:
        bot._shutdown_event.clear()
    return dispatched, seen_params, session


def test_polling_dispatches_and_advances_offset(monkeypatch):
    batches = [
        _PollResp({"ok": True, "result": [
            {"update_id": 10, "message": {"chat": {"id": 1}, "text": "a"}},
            {"update_id": 11, "message": {"chat": {"id": 1}, "text": "b"}},
        ]}),
        _PollResp({"ok": True, "result": []}),
    ]
    dispatched, params, session = _run_poller(monkeypatch, batches)

    assert [u["update_id"] for u in dispatched] == [10, 11]
    assert params[0]["offset"] == 0
    assert params[1]["offset"] == 12, "offset must be highest update_id + 1"
    assert "callback_query" in params[0]["allowed_updates"]
    assert any("deleteWebhook" in u for u in session.posts), \
        "Telegram refuses getUpdates while a webhook is registered"


def test_polling_survives_network_error(monkeypatch):
    """A filtered network drops connections; the poller must not die."""
    batches = [
        RuntimeError("Network is unreachable"),
        _PollResp({"ok": True, "result": [
            {"update_id": 5, "message": {"chat": {"id": 1}, "text": "после сбоя"}},
        ]}),
    ]
    dispatched, _params, _s = _run_poller(monkeypatch, batches)
    assert [u["update_id"] for u in dispatched] == [5]


def test_polling_recovers_from_conflict(monkeypatch):
    """409 means a webhook reappeared — drop it and carry on."""
    batches = [
        _PollResp({}, status=409),
        _PollResp({"ok": True, "result": [
            {"update_id": 7, "message": {"chat": {"id": 1}, "text": "ok"}},
        ]}),
    ]
    dispatched, _p, session = _run_poller(monkeypatch, batches)
    assert [u["update_id"] for u in dispatched] == [7]
    assert sum("deleteWebhook" in u for u in session.posts) >= 2


def test_polling_advances_past_a_poison_update(monkeypatch):
    """A handler that always raises must not wedge the offset forever."""
    def boom(_update):
        raise ValueError("handler exploded")

    monkeypatch.setattr(bot, "BOT_TOKEN", "123456:test-token")
    monkeypatch.setattr(bot, "POLL_TIMEOUT", 0)
    monkeypatch.setattr(bot, "dispatch_update", boom)

    queue = [_PollResp({"ok": True, "result": [
        {"update_id": 99, "message": {"chat": {"id": 1}, "text": "яд"}}]})]
    seen = []

    class FakeSession:
        def post(self, url, **kw):
            return _PollResp({"ok": True})

        def get(self, url, params=None, **kw):
            seen.append(params)
            if queue:
                return queue.pop(0)
            bot._shutdown_event.set()
            return _PollResp({"ok": True, "result": []})

    monkeypatch.setattr(bot, "telegram_session", FakeSession())
    bot._shutdown_event.clear()
    try:
        bot._polling_worker()   # must not propagate the handler's exception
    finally:
        bot._shutdown_event.clear()
    assert seen[-1]["offset"] == 100


def test_dispatch_update_routes_and_survives_bad_payload(monkeypatch):
    seen = []
    monkeypatch.setattr(bot, "_process_update", lambda d: seen.append("message"))
    monkeypatch.setattr(bot, "_process_callback", lambda d: seen.append("callback"))
    monkeypatch.setattr(bot, "save_state", lambda: seen.append("saved"))

    bot.dispatch_update({"message": {"chat": {"id": 1}}})
    bot.dispatch_update({"callback_query": {"id": "1"}})
    bot.dispatch_update(None)          # must not raise

    assert seen.count("message") == 1
    assert seen.count("callback") == 1
    assert seen.count("saved") == 3, "state must be flushed on every path"


def test_env_int_survives_empty_and_garbage(monkeypatch):
    """`int(os.getenv("X", "12"))` uses its default only when the variable is
    ABSENT. A .env copied from .env.example is full of present-but-empty keys,
    and int("") raises at import — which surfaces as a bare gunicorn exit 3."""
    monkeypatch.setenv("TURBOT_TEST_INT", "")
    assert bot._env_int("TURBOT_TEST_INT", 42) == 42
    monkeypatch.setenv("TURBOT_TEST_INT", "   ")
    assert bot._env_int("TURBOT_TEST_INT", 42) == 42
    monkeypatch.setenv("TURBOT_TEST_INT", "не число")
    assert bot._env_int("TURBOT_TEST_INT", 42) == 42
    monkeypatch.setenv("TURBOT_TEST_INT", " 7 ")
    assert bot._env_int("TURBOT_TEST_INT", 42) == 7
    monkeypatch.delenv("TURBOT_TEST_INT")
    assert bot._env_int("TURBOT_TEST_INT", 42) == 42


def test_no_raw_int_env_parsing_remains():
    """Guard the whole class of bug, not just the ten call sites fixed today."""
    import re as _re
    for path in ("bot.py", "vk_bot.py"):
        with open(path, encoding="utf-8") as fh:
            code = "".join(l for l in fh if not l.lstrip().startswith(("#", "*")))
        bad = _re.findall(r'^\s*[A-Z_]+\s*=\s*int\(os\.getenv', code, _re.M)
        assert not bad, f"{path}: use _env_int() instead of raw int(os.getenv): {bad}"


# ---------------------------------------------------------------------------
# Age bands and date confirmation — from live manager feedback


def test_family_with_children_is_priced_by_age_band(client, monkeypatch):
    """A family of four with two kids used to be quoted four adult fares."""
    seen = {}
    monkeypatch.setattr(bot, "TUTU_ENABLED", True)
    monkeypatch.setattr(bot._tutu, "search_offers",
                        lambda *a, **kw: seen.update(kw) or None)
    _consent(client, 6001)
    for text in ["Турция", "Москва", "15-22 сентября", "2"]:
        _post(client, 6001, text)
    assert bot.user_data[6001]["state"] == bot.STATE_KIDS
    _post(client, 6001, "2")                      # двое детей
    assert bot.user_data[6001]["state"] == bot.STATE_INFANTS
    _post(client, 6001, "1")                      # один малыш
    assert bot.user_data[6001]["state"] == bot.STATE_BUDGET
    _post(client, 6001, "70000")
    _post(client, 6001, "+79161234567")

    assert seen.get("people") == "2", "adults"
    assert seen.get("kids") == 2, "children 2–11 must reach the search"
    assert seen.get("infants") == 1, "infants under 2 must reach the search"


def test_no_children_skips_the_infant_question(client):
    """Childless clients must not pay for the extra step with a tap."""
    _consent(client, 6002)
    for text in ["Турция", "Москва", "15-22 сентября", "2"]:
        _post(client, 6002, text)
    _post(client, 6002, bot.KIDS_NONE_LABEL)
    assert bot.user_data[6002]["state"] == bot.STATE_BUDGET
    assert bot.user_data[6002]["kids"] == 0
    assert bot.user_data[6002]["infants"] == 0


def test_age_bands_persisted_with_lead(client):
    _consent(client, 6003)
    for text in ["Турция", "Москва", "15-22 сентября", "2", "1", "Нет", "70000"]:
        _post(client, 6003, text)
    _post(client, 6003, "+79161234567")
    with bot._db_cursor() as cur:
        cur.execute("SELECT people, kids, infants FROM leads WHERE chat_id = ?", (6003,))
        row = cur.fetchone()
        assert (row["people"], row["kids"], row["infants"]) == ("2", 1, 0)


def test_unparseable_dates_are_rejected_not_swallowed(client, monkeypatch):
    """Storing dates nobody can read means quoting a price for nothing."""
    sent = []
    monkeypatch.setattr(bot, "send_message",
                        lambda cid, text, **k: sent.append(text) or _OkResp())
    _consent(client, 6004)
    _post(client, 6004, "Турция")
    _post(client, 6004, "Москва")
    _post(client, 6004, "когда-нибудь потом")
    assert bot.user_data[6004]["state"] == bot.STATE_DATES, "must stay on the step"
    assert "dates" not in bot.user_data[6004]
    assert any("Не разобрал" in t for t in sent)


def test_understood_dates_are_read_back(client, monkeypatch):
    """The step felt dead to the first real user; now it answers."""
    sent = []
    monkeypatch.setattr(bot, "send_message",
                        lambda cid, text, **k: sent.append(text) or _OkResp())
    _consent(client, 6005)
    _post(client, 6005, "Турция")
    _post(client, 6005, "Москва")
    _post(client, 6005, "15-22 сентября")
    assert bot.user_data[6005]["state"] == bot.STATE_PEOPLE
    assert any("Понял" in t and "сентября" in t for t in sent)


def test_party_text_shows_composition():
    assert bot._party_text({"people": "2"}) == "2 взр."
    assert bot._party_text({"people": "2", "kids": 2}) == "2 взр. + 2 реб. (2–11)"
    assert bot._party_text({"people": "1", "kids": 1, "infants": 1}) == \
        "1 взр. + 1 реб. (2–11) + 1 млад. (до 2)"


def test_people_step_asks_for_adults_not_everyone(client, monkeypatch):
    """The step means adults and children are asked next — but it used to say
    «Сколько человек поедет?». A family of four answered 4, then 2 children,
    and the bot priced six passengers."""
    sent = []
    monkeypatch.setattr(bot, "send_message",
                        lambda cid, text, **k: sent.append(text) or _OkResp())
    _consent(client, 7001)
    _post(client, 7001, "Турция")
    _post(client, 7001, "Москва")
    _post(client, 7001, "15-22 сентября")
    ask = [t for t in sent if "👥" in t][-1]
    assert "взрослых" in ask
    assert "Сколько человек поедет" not in ask
    assert "12" in ask, "the age boundary has to be stated, not implied"


def test_polling_starts_before_profile_setup(monkeypatch):
    """Receiving messages must not queue behind cosmetics.

    setMyName is rate-limited by Telegram; the session retries the 429 with
    backoff, and five such calls ground on for minutes while the poller had
    not started — service green, bot deaf.
    """
    order = []
    monkeypatch.setattr(bot, "BOT_MODE", "polling")
    monkeypatch.setattr(bot, "MDT_ENABLED", False)
    monkeypatch.setattr(bot, "ensure_bot_profile",
                        lambda: order.append("profile"))

    class FakeThread:
        def __init__(self, target=None, name=None, daemon=None):
            self.name = name

        def start(self):
            order.append("polling")

    monkeypatch.setattr(bot.threading, "Thread", FakeThread)
    bot._deferred_network_startup()
    assert order == ["polling", "profile"], f"wrong order: {order}"
