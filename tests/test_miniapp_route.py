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
    with bot._db_cursor(commit=True) as db:
        db.execute('DELETE FROM miniapp_submissions')
    yield
    for worker in threading.enumerate():
        if worker.name.startswith("miniapp-contact-"):
            worker.join(timeout=5)
            assert not worker.is_alive()


def test_save_state_defers_busy_dialog_without_restoring_cancelled_draft(monkeypatch):
    uid = 88991
    locked = threading.Event()
    release = threading.Event()
    writes = []
    monkeypatch.setattr(bot, 'set_session', lambda cid, info: writes.append(('save', cid)))
    monkeypatch.setattr(bot, 'delete_session', lambda cid: writes.append(('delete', cid)))
    bot.user_data[uid] = {'state': bot.STATE_CONTACT}
    with bot._lock:
        bot._dirty_sessions.clear()
        bot._dirty_users.clear()
        bot._dirty_sessions.add(uid)

    def cancel_while_locked():
        with bot._dialog_locks[uid % len(bot._dialog_locks)]:
            locked.set()
            assert release.wait(5)
            bot.user_data.pop(uid, None)

    worker = threading.Thread(target=cancel_while_locked)
    worker.start()
    try:
        assert locked.wait(3)
        bot.save_state()
        assert writes == []
        assert uid in bot._dirty_sessions
    finally:
        release.set()
        worker.join(5)
    assert not worker.is_alive()
    bot.save_state()
    assert writes == [('delete', uid)]


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
        return {'ok': True, 'result': {'message_id': len(sent)}}

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


@pytest.mark.parametrize('outcome', ['before_send','contact_missing','already_delivered','cancel_before_recovery'])
def test_restart_recovers_only_unconfirmed_miniapp_prompts(monkeypatch, outcome):
    uid = 88291
    monkeypatch.setattr(bot, 'BOT_TOKEN', BOT_TOKEN)
    monkeypatch.setattr(bot, 'MINI_APP_ORIGIN', ORIGIN)
    monkeypatch.setattr(bot, 'save_lead', lambda *a, **k: pytest.fail('notification recovery created a lead'))
    queue = bot._queue_miniapp_contact
    monkeypatch.setattr(bot, '_queue_miniapp_contact', lambda *a: None)
    sent, delivered = [], threading.Event()
    def send(cid, text, **kwargs):
        if 'Параметры поездки получены' in text or 'Как удобнее связаться' in text:
            sent.append(text)
            if outcome == 'contact_missing' and len(sent) == 2:
                return None
            if 'Как удобнее связаться' in text:
                delivered.set()
        return {'ok':True,'result':{'message_id':len(sent)+100}}
    monkeypatch.setattr(bot, 'send_message', send)
    bot.user_data.pop(uid,None)
    with bot._db_cursor(commit=True) as db:
        db.execute('DELETE FROM sessions WHERE chat_id=?',(uid,))
    body = dict(initData=_signed_init_data(uid), payload=_payload(), submissionId='recover-contact-notification')
    assert bot.app.test_client().post('/miniapp/submit',json=body,headers={'Origin':ORIGIN}).status_code == 200
    if outcome in ('contact_missing','already_delivered'):
        bot._notify_miniapp_contact(uid,bot.user_data[uid])
    if outcome == 'cancel_before_recovery':
        bot.handle_cancel(uid)
    else:
        bot.user_data.pop(uid)
        restored = bot.get_session(uid)
        restored['kids_ages'] = bot._ages_from_db(restored.get('kids_ages'))
        bot.user_data[uid] = restored
    monkeypatch.setattr(bot,'_queue_miniapp_contact',queue)
    bot._recover_miniapp_notifications()
    if outcome != 'cancel_before_recovery':
        assert delivered.wait(3)
        for worker in threading.enumerate():
            if worker.name.startswith('miniapp-contact-'):
                worker.join(timeout=3)
        with bot._db_cursor() as db:
            row = db.execute('SELECT notice_id,contact_id FROM miniapp_submissions WHERE user_id=?',(uid,)).fetchone()
            assert row['notice_id'] and row['contact_id']
    assert len(sent) == (3 if outcome == 'contact_missing' else 0 if outcome == 'cancel_before_recovery' else 2)
    bot.delete_user_data(uid)


@pytest.mark.parametrize('action', ['restart', 'cancel', 'replace', 'change_payload', 'edit_in_bot', 'receipt_crash_restart', 'receipt_crash_cancel'])
def test_durable_miniapp_receipt_reconciles_restart_and_fences_old_trips(monkeypatch, action):
    uid = 88191
    monkeypatch.setattr(bot, 'BOT_TOKEN', BOT_TOKEN)
    monkeypatch.setattr(bot, 'MINI_APP_ORIGIN', ORIGIN)
    monkeypatch.setattr(bot, '_notify_miniapp_contact', lambda *a: None)
    monkeypatch.setattr(bot, 'send_message', lambda *a, **k: None)
    monkeypatch.setattr(bot, 'save_lead', lambda *a, **k: pytest.fail('not an enquiry'))
    body = dict(initData=_signed_init_data(uid), payload=_payload(), submissionId='durable-miniapp-operation-1')
    client = bot.app.test_client()
    with bot._db_cursor(commit=True) as db:
        db.execute('DELETE FROM sessions WHERE chat_id=?', (uid,))
    bot.user_data.pop(uid, None)
    assert client.post('/miniapp/submit', json=body, headers={'Origin': ORIGIN}).status_code == 200
    if action.startswith('receipt_crash'):
        with bot._db_cursor(commit=True) as db:
            db.execute('DELETE FROM miniapp_submissions WHERE user_id=?',(uid,))
    if action in ('restart', 'receipt_crash_restart'):
        bot.user_data.pop(uid)
    elif action in ('cancel', 'receipt_crash_cancel'):
        bot.handle_cancel(uid)
        bot.user_data.pop(uid, None)
    elif action == 'replace':
        newer = dict(body, submissionId='durable-miniapp-operation-2', payload=dict(_payload(), budgetMaxRub=350001))
        assert client.post('/miniapp/submit', json=newer, headers={'Origin': ORIGIN}).status_code == 200
    elif action == 'change_payload':
        body['payload']['budgetMaxRub'] = 350001
    else:
        bot.user_data[uid]['budget'] = 350001
        bot.set_session(uid, bot.user_data[uid])
    before = bot.get_session(uid)
    reply = client.post('/miniapp/submit', json=body, headers={'Origin': ORIGIN})
    if action in ('restart', 'receipt_crash_restart'):
        assert reply.status_code == 200 and reply.json['duplicate'] is True
    else:
        assert reply.status_code == 409
    assert bot.get_session(uid) == before
    bot.delete_user_data(uid)
    with bot._db_cursor() as db:
        assert db.execute('SELECT count(*) FROM miniapp_submissions WHERE user_id=?',(uid,)).fetchone()[0] == 0


def test_concurrent_miniapp_retry_waits_for_first_durable_save(monkeypatch):
    chat_id = 88025
    submission_id = "018f47ab-8f86-7a51-bf2a-5d9f72863f12"
    save_started = threading.Event()
    release_save = threading.Event()
    responses = []
    persisted = []
    sent = []
    notification_done = threading.Event()

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
    def confirmed_message(cid, text, **kwargs):
        sent.append((cid,text))
        if 'Как удобнее связаться' in text:
            notification_done.set()
        return {'ok': True, 'result': {'message_id': len(sent)}}
    monkeypatch.setattr(bot, "send_message", confirmed_message)
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
        assert notification_done.wait(3)
        bot.user_data.pop(chat_id, None)

    assert not first.is_alive()
    assert not second.is_alive()
    assert len(persisted) == 1
    assert sorted(item["duplicate"] for item in responses) == [False, True]
    assert sum("Параметры поездки получены" in text for _, text in sent) == 1
