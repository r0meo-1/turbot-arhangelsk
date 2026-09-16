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
