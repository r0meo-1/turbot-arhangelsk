import hashlib
import hmac
import json
import threading
import time
from urllib.parse import urlencode

import bot
import pytest


BOT_TOKEN = "123456789:test_bot_token_for_route"
ORIGIN = "https://r0meo-1.github.io"


@pytest.fixture(autouse=True)
def finish_miniapp_notifications_before_restoring_mocks(monkeypatch):
    yield
    for worker in threading.enumerate():
        if worker.name.startswith("miniapp-contact-"):
            worker.join(timeout=5)
            assert not worker.is_alive()


def _signed_init_data(user_id=88001, start_param=None):
    fields = {
        "auth_date": str(int(time.time())),
        "query_id": "AAHdF6IQAAAAAN0XohDhrOrc",
        "user": json.dumps({"id": user_id, "first_name": "Roma", "username": "tester"}, separators=(",", ":")),
    }
    if start_param is not None:
        fields["start_param"] = start_param
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
    contact_sent = threading.Event()

    def capture_message(cid, text, **kwargs):
        sent.append((cid, text))
        if "Как удобнее связаться" in text:
            contact_sent.set()

    monkeypatch.setattr(bot, "BOT_TOKEN", BOT_TOKEN)
    monkeypatch.setattr(bot, "MINI_APP_ORIGIN", ORIGIN)
    monkeypatch.setattr(bot, "_touch_user", lambda *args, **kwargs: None)
    monkeypatch.setattr(bot, "set_consent", lambda *args, **kwargs: None)
    monkeypatch.setattr(bot, "_mark_dirty", lambda *args, **kwargs: None)
    monkeypatch.setattr(bot, "save_state", lambda: None)
    monkeypatch.setattr(bot, "send_message", capture_message)
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
    assert contact_sent.wait(3)
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


def test_menu_miniapp_uses_signed_start_param_for_attribution(monkeypatch):
    chat_id = 88009
    monkeypatch.setattr(bot, "BOT_TOKEN", BOT_TOKEN)
    monkeypatch.setattr(bot, "MINI_APP_ORIGIN", ORIGIN)
    monkeypatch.setattr(bot, "_touch_user", lambda *args, **kwargs: None)
    monkeypatch.setattr(bot, "set_consent", lambda *args, **kwargs: None)
    monkeypatch.setattr(bot, "_mark_dirty", lambda *args, **kwargs: None)
    monkeypatch.setattr(bot, "save_state", lambda: None)
    monkeypatch.setattr(bot, "send_message", lambda *args, **kwargs: None)
    bot.user_data.pop(chat_id, None)

    response = bot.app.test_client().post(
        "/miniapp/submit",
        json={"initData": _signed_init_data(chat_id, "Video_Pain"), "payload": _payload()},
        headers={"Origin": ORIGIN},
    )

    assert response.status_code == 200
    assert bot.user_data[chat_id]["source_tag"] == "video_pain"
    bot.user_data.pop(chat_id, None)


def test_menu_miniapp_acknowledges_saved_draft_while_telegram_is_slow(monkeypatch):
    chat_id = 88021
    telegram_started = threading.Event()
    release_telegram = threading.Event()
    response_done = threading.Event()
    contact_sent = threading.Event()
    persisted = []
    responses = []

    def slow_send(_chat_id, text, **kwargs):
        telegram_started.set()
        assert release_telegram.wait(10), "test did not release Telegram transport"
        if "Как удобнее связаться" in text:
            contact_sent.set()

    monkeypatch.setattr(bot, "BOT_TOKEN", BOT_TOKEN)
    monkeypatch.setattr(bot, "MINI_APP_ORIGIN", ORIGIN)
    monkeypatch.setattr(bot, "_touch_user", lambda *args, **kwargs: None)
    monkeypatch.setattr(bot, "set_consent", lambda *args, **kwargs: None)
    monkeypatch.setattr(bot, "_mark_dirty", lambda *args, **kwargs: None)
    monkeypatch.setattr(bot, "save_state", lambda: persisted.append(dict(bot.user_data[chat_id])))
    monkeypatch.setattr(bot, "send_message", slow_send)
    bot.user_data.pop(chat_id, None)

    def submit():
        try:
            responses.append(bot.app.test_client().post(
                "/miniapp/submit",
                json={"initData": _signed_init_data(chat_id), "payload": _payload()},
                headers={"Origin": ORIGIN},
            ))
        finally:
            response_done.set()

    request_thread = threading.Thread(target=submit)
    request_thread.start()
    try:
        assert telegram_started.wait(3)
        acknowledged_before_telegram = response_done.wait(1)
    finally:
        release_telegram.set()
        request_thread.join(timeout=5)
        contact_completed = contact_sent.wait(3)
        bot.user_data.pop(chat_id, None)

    assert acknowledged_before_telegram, "saved draft response waited for Telegram I/O"
    assert len(persisted) == 1
    assert persisted[0]["state"] == bot.STATE_CONTACT
    assert persisted[0]["nights"] == 10
    assert responses[0].status_code == 200
    assert responses[0].get_json()["ok"] is True
    assert contact_completed


def test_menu_miniapp_does_not_acknowledge_or_notify_when_storage_fails(monkeypatch):
    chat_id = 88022
    sent = []

    def failed_save():
        raise RuntimeError("synthetic database failure")

    monkeypatch.setattr(bot, "BOT_TOKEN", BOT_TOKEN)
    monkeypatch.setattr(bot, "MINI_APP_ORIGIN", ORIGIN)
    monkeypatch.setattr(bot, "_touch_user", lambda *args, **kwargs: None)
    monkeypatch.setattr(bot, "set_consent", lambda *args, **kwargs: None)
    monkeypatch.setattr(bot, "_mark_dirty", lambda *args, **kwargs: None)
    monkeypatch.setattr(bot, "save_state", failed_save)
    monkeypatch.setattr(bot, "send_message", lambda *args, **kwargs: sent.append(args))
    try:
        response = bot.app.test_client().post(
            "/miniapp/submit",
            json={"initData": _signed_init_data(chat_id), "payload": _payload()},
            headers={"Origin": ORIGIN},
        )
        assert response.status_code == 500
        assert response.get_json()["ok"] is False
        assert sent == []
    finally:
        bot.user_data.pop(chat_id, None)


def test_menu_miniapp_retry_reuses_durable_submission(monkeypatch):
    chat_id = 88023
    submission_id = "018f47ab-8f86-7a51-bf2a-5d9f72863f11"
    persisted = []
    sent = []
    contact_sent = threading.Event()

    def capture_message(cid, text, **kwargs):
        sent.append((cid, text))
        if "Как удобнее связаться" in text:
            contact_sent.set()

    monkeypatch.setattr(bot, "BOT_TOKEN", BOT_TOKEN)
    monkeypatch.setattr(bot, "MINI_APP_ORIGIN", ORIGIN)
    monkeypatch.setattr(bot, "_touch_user", lambda *args, **kwargs: None)
    monkeypatch.setattr(bot, "set_consent", lambda *args, **kwargs: None)
    monkeypatch.setattr(bot, "_mark_dirty", lambda *args, **kwargs: None)
    monkeypatch.setattr(bot, "save_state", lambda: persisted.append(dict(bot.user_data[chat_id])))
    monkeypatch.setattr(bot, "send_message", capture_message)
    bot.user_data.pop(chat_id, None)

    request_body = {
        "initData": _signed_init_data(chat_id),
        "payload": _payload(),
        "submissionId": submission_id,
    }
    try:
        first = bot.app.test_client().post(
            "/miniapp/submit", json=request_body, headers={"Origin": ORIGIN}
        )
        second = bot.app.test_client().post(
            "/miniapp/submit", json=request_body, headers={"Origin": ORIGIN}
        )
        assert contact_sent.wait(3)
    finally:
        bot.user_data.pop(chat_id, None)

    assert first.status_code == 200
    assert first.get_json()["duplicate"] is False
    assert second.status_code == 200
    assert second.get_json()["duplicate"] is True
    assert len(persisted) == 1
    assert persisted[0]["miniapp_submission_id"] == submission_id
    assert sum("Параметры поездки получены" in text for _, text in sent) == 1
    assert sum("Как удобнее связаться" in text for _, text in sent) == 1


def test_menu_miniapp_rejects_invalid_submission_id(monkeypatch):
    chat_id = 88024
    monkeypatch.setattr(bot, "BOT_TOKEN", BOT_TOKEN)
    monkeypatch.setattr(bot, "MINI_APP_ORIGIN", ORIGIN)

    response = bot.app.test_client().post(
        "/miniapp/submit",
        json={
            "initData": _signed_init_data(chat_id),
            "payload": _payload(),
            "submissionId": "short",
        },
        headers={"Origin": ORIGIN},
    )

    assert response.status_code == 400
    assert response.get_json() == {"ok": False, "error": "Submission ID is invalid"}
    assert chat_id not in bot.user_data


def test_concurrent_miniapp_retry_waits_for_first_durable_save(monkeypatch):
    chat_id = 88025
    submission_id = "018f47ab-8f86-7a51-bf2a-5d9f72863f12"
    save_started = threading.Event()
    release_save = threading.Event()
    responses = []
    persisted = []
    sent = []

    def slow_save():
        save_started.set()
        assert release_save.wait(5), "test did not release durable save"
        persisted.append(dict(bot.user_data[chat_id]))

    monkeypatch.setattr(bot, "BOT_TOKEN", BOT_TOKEN)
    monkeypatch.setattr(bot, "MINI_APP_ORIGIN", ORIGIN)
    monkeypatch.setattr(bot, "_touch_user", lambda *args, **kwargs: None)
    monkeypatch.setattr(bot, "set_consent", lambda *args, **kwargs: None)
    monkeypatch.setattr(bot, "_mark_dirty", lambda *args, **kwargs: None)
    monkeypatch.setattr(bot, "save_state", slow_save)
    monkeypatch.setattr(bot, "send_message", lambda cid, text, **kwargs: sent.append((cid, text)))
    bot.user_data.pop(chat_id, None)
    request_body = {
        "initData": _signed_init_data(chat_id),
        "payload": _payload(),
        "submissionId": submission_id,
    }

    def submit():
        response = bot.app.test_client().post(
            "/miniapp/submit", json=request_body, headers={"Origin": ORIGIN}
        )
        responses.append(response.get_json())

    first = threading.Thread(target=submit)
    second = threading.Thread(target=submit)
    first.start()
    try:
        assert save_started.wait(3)
        second.start()
        assert second.is_alive(), "retry bypassed the in-flight durable save"
    finally:
        release_save.set()
        first.join(timeout=5)
        second.join(timeout=5)
        bot.user_data.pop(chat_id, None)

    assert not first.is_alive()
    assert not second.is_alive()
    assert len(persisted) == 1
    assert sorted(item["duplicate"] for item in responses) == [False, True]
    assert sum("Параметры поездки получены" in text for _, text in sent) == 1
