"""Unit tests for the TurBot VK webhook flow and helpers."""

import os
import json
import time
import tempfile

# Configure the VK bot before it is imported.
os.environ.setdefault("VK_ACCESS_TOKEN", "dummy-token")
os.environ.setdefault("VK_GROUP_ID", "999")
os.environ.setdefault("VK_CONFIRMATION", "confirm123")
os.environ.setdefault("ADMIN_ID", "999")
os.environ.setdefault("DIALOG_TIMEOUT_HOURS", "0")
os.environ.setdefault("SYNC_COMPLETION", "true")  # run MDT/AI inline in tests
os.environ.setdefault("AI_MODE", "template")
os.environ.setdefault("CONSENT_MODE", "strict")  # classic consent in unit tests
os.environ.setdefault(
    "DATABASE_PATH",
    os.path.join(tempfile.gettempdir(), f"vk_turbot_test_{os.getpid()}.sqlite"),
)

import vk_bot as bot

import pytest


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


@pytest.fixture
def client():
    return bot.app.test_client()


def _vk_message(user_id, text=None):
    """Build a minimal VK message_new event."""
    msg = {"peer_id": user_id, "from_id": user_id}
    if text is not None:
        msg["text"] = text
    return {"type": "message_new", "object": {"message": msg}, "group_id": 999}


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
    assert data["vk_token_configured"] is True


def test_confirmation(client):
    resp = client.post("/vk/webhook",
                       json={"type": "confirmation", "group_id": 999},
                       content_type="application/json")
    assert resp.status_code == 200
    assert resp.get_data(as_text=True) == "confirm123"


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
    for text in ["Египет", "15-22 июня", "2", "60000"]:
        _post(client, 222, text)
    assert bot.user_data[222]["state"] == bot.STATE_CONTACT
    _post(client, 222, "+79161234567")
    assert 222 not in bot.user_data
    with bot._db_cursor() as cur:
        cur.execute("SELECT phone, destination FROM leads WHERE chat_id = ?", (222,))
        row = cur.fetchone()
    assert row is not None
    assert row[0] == "+79161234567"
    assert "Египет" in (row[1] or "")


def test_soft_mode_and_vk_contact(client, monkeypatch):
    monkeypatch.setattr(bot, "CONSENT_MODE", "soft")
    _post(client, 901, "Начать")
    assert bot.user_data[901]["state"] == bot.STATE_CONSENT
    _post(client, 901, bot.START_BUTTON_TEXT)
    assert bot.has_consent(901)
    assert bot.user_data[901]["state"] == bot.STATE_DESTINATION
    for text in ["Турция", bot.DATE_PRESETS[0][0], "2", bot.BUDGET_PRESETS[1][0]]:
        _post(client, 901, text)
    assert bot.user_data[901]["state"] == bot.STATE_CONTACT
    assert bot.user_data[901]["budget"] == bot.BUDGET_PRESETS[1][1]
    _post(client, 901, bot.CONTACT_VK_CHAT_LABEL)
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


def test_back_button(client):
    _post(client, 333, "Начать")
    _post(client, 333, bot.CONSENT_YES_TEXT)
    _post(client, 333, "Египет")
    assert bot.user_data[333]["state"] == bot.STATE_DATES
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
