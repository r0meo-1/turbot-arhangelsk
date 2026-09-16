import json

import vk_bot as bot
from shared.vk_miniapp import MINIAPP_BUTTON_TEXT, build_open_app_button


def test_open_app_button_uses_existing_vk_app_and_community():
    button = build_open_app_button("54475121", 240310110, enabled=True)

    assert button == {
        "action": {
            "type": "open_app",
            "app_id": 54475121,
            "owner_id": -240310110,
            "label": MINIAPP_BUTTON_TEXT,
            "hash": "bot",
        }
    }


def test_open_app_button_is_hidden_until_runtime_is_configured():
    assert build_open_app_button("", 240310110, enabled=True) is None
    assert build_open_app_button("54475121", 0, enabled=True) is None
    assert build_open_app_button("54475121", 240310110, enabled=False) is None


def test_soft_start_keyboard_wires_native_open_app_button(monkeypatch):
    monkeypatch.setenv("VK_MINI_APP_ID", "54475121")
    monkeypatch.setenv("VK_MINI_APP_SECRET", "test-secret")
    monkeypatch.setattr(bot, "VK_GROUP_ID", 240310110)

    keyboard = json.loads(bot._soft_start_keyboard())

    assert keyboard["buttons"][0][0] == {
        "action": {
            "type": "open_app",
            "app_id": 54475121,
            "owner_id": -240310110,
            "label": MINIAPP_BUTTON_TEXT,
            "hash": "bot",
        }
    }
    assert keyboard["buttons"][1][0]["action"]["label"] == bot.START_BUTTON_TEXT


def test_soft_start_keyboard_hides_open_app_without_secret(monkeypatch):
    monkeypatch.setenv("VK_MINI_APP_ID", "54475121")
    monkeypatch.delenv("VK_MINI_APP_SECRET", raising=False)
    monkeypatch.setattr(bot, "VK_GROUP_ID", 240310110)

    keyboard = json.loads(bot._soft_start_keyboard())

    assert len(keyboard["buttons"]) == 1
    assert keyboard["buttons"][0][0]["action"]["label"] == bot.START_BUTTON_TEXT


def test_soft_start_handler_delivers_native_open_app_button(monkeypatch):
    user_id = 424243
    sent = []

    monkeypatch.setenv("VK_MINI_APP_ID", "54475121")
    monkeypatch.setenv("VK_MINI_APP_SECRET", "test-secret")
    monkeypatch.setattr(bot, "VK_GROUP_ID", 240310110)
    monkeypatch.setattr(bot, "CONSENT_MODE", "soft")
    monkeypatch.setattr(bot, "has_consent", lambda uid: False)
    monkeypatch.setattr(bot, "_mark_dirty", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        bot,
        "send_message",
        lambda uid, text, keyboard=None, **kwargs: sent.append((uid, text, keyboard)),
    )
    bot.user_data.pop(user_id, None)

    bot.handle_start(user_id, "Тест")

    assert bot.user_data[user_id]["state"] == bot.STATE_CONSENT
    assert len(sent) == 1
    assert sent[0][0] == user_id
    keyboard = json.loads(sent[0][2])
    assert keyboard["buttons"][0][0]["action"] == {
        "type": "open_app",
        "app_id": 54475121,
        "owner_id": -240310110,
        "label": MINIAPP_BUTTON_TEXT,
        "hash": "bot",
    }
    assert keyboard["buttons"][1][0]["action"]["label"] == bot.START_BUTTON_TEXT

    bot.user_data.pop(user_id, None)
