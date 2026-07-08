"""Unit tests for the TurBot webhook flow and helpers."""

import os
import time
import tempfile

# Configure the bot before it is imported.
os.environ.setdefault("BOT_TOKEN", "dummy-token")
os.environ.setdefault("ADMIN_ID", "999")
os.environ.setdefault("TELEGRAM_SECRET_TOKEN", "secret123")
os.environ.setdefault("DIALOG_TIMEOUT_HOURS", "0")  # disable background worker
os.environ.setdefault("STATE_FILE", ":memory:")  # not used when save_state is mocked
os.environ.setdefault(
    "DATABASE_PATH",
    os.path.join(tempfile.gettempdir(), f"turbot_test_{os.getpid()}.sqlite"),
)

import bot

import pytest


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
    monkeypatch.setattr(bot, "send_message", lambda *a, **k: None)
    monkeypatch.setattr(bot, "send_typing", lambda *a, **k: None)
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


def test_start_creates_dialog(client):
    resp = _post(client, 111, "/start")
    assert resp.status_code == 200
    assert 111 in bot.user_data
    assert bot.user_data[111]["state"] == bot.STATE_DESTINATION


def test_dialog_completion_with_contact(client):
    # Walk through the whole flow.
    for text in ["/start", "🏖 Египет", "15-22 июня", "2", "60000"]:
        _post(client, 222, text)

    info = bot.user_data.get(222)
    assert info is not None
    assert info["state"] == bot.STATE_PHONE

    # Finish by sharing a contact.
    resp = _post(client, 222, contact={"phone_number": "79161234567", "user_id": 222})
    assert resp.status_code == 200
    assert 222 not in bot.user_data


def test_back_button(client):
    _post(client, 333, "/start")
    _post(client, 333, "🏖 Египет")
    assert bot.user_data[333]["state"] == bot.STATE_DATES

    _post(client, 333, "◀️ Назад")
    assert bot.user_data[333]["state"] == bot.STATE_DESTINATION


def test_cancel_button(client):
    _post(client, 444, "/start")
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
    text = bot._template_selection("Турция", "15-22 июня", "2", "60000")
    assert "Турция" in text
    assert "15-22 июня" in text
    assert "60000" in text
    assert "Возьмите с собой" in text


def test_template_selection_fallback_for_unknown_destination():
    text = bot._template_selection("Шри-Ланка", "1-10 марта", "3", "80000")
    assert "Шри-Ланка" in text
    assert "отличное направление" in text


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
