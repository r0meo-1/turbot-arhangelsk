import hashlib
import hmac
import json
import time
from urllib.parse import urlencode

import bot


BOT_TOKEN = "123456789:test_bot_token_for_route"
ORIGIN = "https://r0meo-1.github.io"


def _signed_init_data(user_id=88001):
    fields = {
        "auth_date": str(int(time.time())),
        "query_id": "AAHdF6IQAAAAAN0XohDhrOrc",
        "user": json.dumps({"id": user_id, "first_name": "Roma", "username": "tester"}, separators=(",", ":")),
    }
    check = "\n".join(f"{key}={fields[key]}" for key in sorted(fields))
    secret = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return urlencode(fields)


def _payload():
    return {
        "type": "trip_request",
        "version": 2,
        "destination": "Вьетнам",
        "departure": "Москва",
        "date": "2099-02-10",
        "nights": 10,
        "adults": 2,
        "children": 1,
        "childrenAges": [7],
        "budgetMaxRub": 270000,
        "directOnly": True,
        "consent": True,
        "source": "telegram_mini_app",
    }


def test_menu_miniapp_submission_creates_contact_step(monkeypatch):
    chat_id = 88001
    sent = []
    monkeypatch.setattr(bot, "BOT_TOKEN", BOT_TOKEN)
    monkeypatch.setattr(bot, "MINI_APP_ORIGIN", ORIGIN)
    monkeypatch.setattr(bot, "_touch_user", lambda *args, **kwargs: None)
    monkeypatch.setattr(bot, "set_consent", lambda *args, **kwargs: None)
    monkeypatch.setattr(bot, "_mark_dirty", lambda *args, **kwargs: None)
    monkeypatch.setattr(bot, "save_state", lambda: None)
    monkeypatch.setattr(bot, "send_message", lambda cid, text, **kwargs: sent.append((cid, text)))
    bot.user_data.pop(chat_id, None)

    response = bot.app.test_client().post(
        "/miniapp/submit",
        json={"initData": _signed_init_data(chat_id), "payload": _payload()},
        headers={"Origin": ORIGIN},
    )

    assert response.status_code == 200
    assert response.get_json()["ok"] is True
    info = bot.user_data[chat_id]
    assert info["state"] == bot.STATE_CONTACT
    assert info["destination"] == "Вьетнам"
    assert info["origin"] == "Москва"
    assert info["kids_ages"] == [7]
    assert info["direct_only"] is True
    assert any("способ связи" in text.lower() for _, text in sent)
    bot.user_data.pop(chat_id, None)


def test_menu_miniapp_rejects_untrusted_origin(monkeypatch):
    monkeypatch.setattr(bot, "BOT_TOKEN", BOT_TOKEN)
    monkeypatch.setattr(bot, "MINI_APP_ORIGIN", ORIGIN)
    response = bot.app.test_client().post(
        "/miniapp/submit",
        json={"initData": _signed_init_data(), "payload": _payload()},
        headers={"Origin": "https://evil.example"},
    )
    assert response.status_code == 403


def test_menu_miniapp_rejects_tampered_init_data(monkeypatch):
    monkeypatch.setattr(bot, "BOT_TOKEN", BOT_TOKEN)
    monkeypatch.setattr(bot, "MINI_APP_ORIGIN", ORIGIN)
    bad = _signed_init_data().replace("tester", "attacker")
    response = bot.app.test_client().post(
        "/miniapp/submit",
        json={"initData": bad, "payload": _payload()},
        headers={"Origin": ORIGIN},
    )
    assert response.status_code == 401
