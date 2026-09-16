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


def test_review_command_restores_miniapp_after_process_cache_loss(monkeypatch):
    from datetime import date, timedelta
    from shared.vk_miniapp import validate_vk_trip

    user_id = 424244
    raw = {
        "type": "trip_request",
        "version": 2,
        "destination": "Таиланд",
        "departure": "Москва",
        "date": (date.today() + timedelta(days=30)).isoformat(),
        "nights": 10,
        "adults": 2,
        "children": 0,
        "childrenAges": [],
        "budgetMaxRub": 270000,
        "consent": True,
    }

    with bot._db_cursor(commit=True) as cur:
        cur.execute("DELETE FROM miniapp_drafts WHERE chat_id = ?", (user_id,))
        cur.execute("DELETE FROM sessions WHERE chat_id = ?", (user_id,))
        cur.execute("DELETE FROM users WHERE chat_id = ?", (user_id,))
    bot.user_data.pop(user_id, None)
    bot.all_users.pop(user_id, None)

    bot._save_miniapp_draft(user_id, validate_vk_trip(raw))
    assert bot._load_miniapp_snapshot(user_id)["destination"] == "Таиланд"

    # Simulate a process restart: durable SQLite rows survive, in-memory state does not.
    bot.user_data.pop(user_id, None)
    bot.all_users.pop(user_id, None)

    reviewed = []
    monkeypatch.setattr(bot, "get_user_name", lambda uid: "Restarted User")
    monkeypatch.setattr(bot, "_mark_dirty", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        bot,
        "_ask_review",
        lambda uid: reviewed.append(dict(bot.user_data[uid])),
    )

    bot._process_message({
        "object": {
            "message": {
                "from_id": user_id,
                "peer_id": user_id,
                "text": "Проверить заявку",
            }
        }
    })

    assert len(reviewed) == 1
    assert reviewed[0]["state"] == bot.STATE_REVIEW
    assert reviewed[0]["destination"] == "Таиланд"
    assert reviewed[0]["origin"] == "Москва"
    assert reviewed[0]["source"] == "vk_mini_app"
    restored = bot.get_session(user_id)
    assert restored["state"] == bot.STATE_REVIEW
    assert restored["budget"] == 270000

    with bot._db_cursor(commit=True) as cur:
        cur.execute("DELETE FROM miniapp_drafts WHERE chat_id = ?", (user_id,))
        cur.execute("DELETE FROM sessions WHERE chat_id = ?", (user_id,))
        cur.execute("DELETE FROM users WHERE chat_id = ?", (user_id,))
    bot.user_data.pop(user_id, None)
    bot.all_users.pop(user_id, None)
