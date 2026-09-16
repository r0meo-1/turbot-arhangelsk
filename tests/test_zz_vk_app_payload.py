import json
import os
import sys
import tempfile

if 'vk_bot' not in sys.modules:
    os.environ['VK_ACCESS_TOKEN'] = 'dummy-token'
    os.environ['VK_GROUP_ID'] = '999'
    os.environ['VK_CONFIRMATION'] = 'confirm123'
    os.environ['VK_SECRET_KEY'] = 'vk-test-secret'
    os.environ['DATABASE_PATH'] = os.path.join(
        tempfile.gettempdir(), f'vk_app_payload_{os.getpid()}.sqlite'
    )
    os.environ['DIALOG_TIMEOUT_HOURS'] = '0'
    os.environ['FOLLOWUP_DELAY_HOURS'] = '0'
    os.environ['DATA_RETENTION_DAYS'] = '0'
    os.environ['VK_MDT_RETRY_ENABLED'] = 'false'
    os.environ['MDT_ENABLED'] = 'false'

import vk_bot as bot


def _clear(user_id):
    bot.user_data.pop(user_id, None)
    bot.all_users.pop(user_id, None)
    with bot._db_cursor(commit=True) as cur:
        cur.execute('DELETE FROM sessions WHERE chat_id = ?', (user_id,))
        cur.execute('DELETE FROM miniapp_drafts WHERE chat_id = ?', (user_id,))
        cur.execute('DELETE FROM users WHERE chat_id = ?', (user_id,))
        cur.execute('DELETE FROM leads WHERE chat_id = ?', (user_id,))


def _event(user_id, payload, *, app_id=54475121, group_id=999):
    return {
        'type': 'app_payload',
        'group_id': group_id,
        'secret': 'vk-test-secret',
        'object': {
            'user_id': user_id,
            'app_id': app_id,
            'payload': json.dumps(payload, ensure_ascii=False),
        },
    }


def test_app_payload_restores_saved_review_without_creating_lead(monkeypatch):
    user_id = 919001
    _clear(user_id)
    monkeypatch.setenv('VK_MINI_APP_ID', '54475121')
    monkeypatch.setattr(bot, 'VK_GROUP_ID', 999)
    review_calls = []
    monkeypatch.setattr(bot, '_ask_review', lambda uid: review_calls.append(uid))

    draft = {
        'destination': 'Таиланд',
        'origin': 'Москва',
        'dates': 'январь',
        'nights': '10',
        'people': '2',
        'kids': 0,
        'kids_ages': [],
        'infants': 0,
        'budget': 250000,
        'budget_scope': 'total',
        'source': 'vk_mini_app',
    }
    bot._save_miniapp_snapshot(user_id, draft)
    bot._process_app_payload(_event(
        user_id, {'command': 'miniapp_review', 'version': 1}
    ))

    assert review_calls == [user_id]
    assert bot.user_data[user_id]['state'] == bot.STATE_REVIEW
    assert bot.user_data[user_id]['destination'] == 'Таиланд'
    assert bot.get_session(user_id)['source'] == 'vk_mini_app'
    with bot._db_cursor() as cur:
        assert cur.execute(
            'SELECT COUNT(*) FROM leads WHERE chat_id = ?', (user_id,)
        ).fetchone()[0] == 0
    _clear(user_id)


def test_app_payload_rejects_wrong_app_community_and_command(monkeypatch):
    user_id = 919002
    _clear(user_id)
    monkeypatch.setenv('VK_MINI_APP_ID', '54475121')
    monkeypatch.setattr(bot, 'VK_GROUP_ID', 999)
    calls = []
    monkeypatch.setattr(bot, '_ask_review', lambda uid: calls.append(uid))
    bot._save_miniapp_snapshot(user_id, {'destination': 'Египет'})

    bot._process_app_payload(_event(
        user_id, {'command': 'miniapp_review', 'version': 1}, app_id=1
    ))
    bot._process_app_payload(_event(
        user_id, {'command': 'miniapp_review', 'version': 1}, group_id=1000
    ))
    bot._process_app_payload(_event(
        user_id, {'command': 'something_else', 'version': 1}
    ))
    assert calls == []
    _clear(user_id)


def test_app_payload_webhook_ack_uses_existing_secret_guard(monkeypatch):
    monkeypatch.setenv('VK_MINI_APP_ID', '54475121')
    monkeypatch.setattr(bot, 'VK_GROUP_ID', 999)
    monkeypatch.setattr(bot, 'VK_SECRET_KEY', 'vk-test-secret')
    dispatched = []

    class ImmediateThread:
        def __init__(self, target, args=(), **kwargs):
            self.target = target
            self.args = args

        def start(self):
            dispatched.append(self.args[0])

    monkeypatch.setattr(bot.threading, 'Thread', ImmediateThread)
    event = _event(919003, {'command': 'miniapp_review', 'version': 1})
    response = bot.app.test_client().post('/vk/webhook', json=event)
    assert response.status_code == 200
    assert response.get_data(as_text=True) == 'ok'
    assert len(dispatched) == 1
    assert dispatched[0]['secret'] == 'vk-test-secret'

    event['secret'] = 'wrong-secret'
    response = bot.app.test_client().post('/vk/webhook', json=event)
    assert response.status_code == 403
    assert len(dispatched) == 1
    assert dispatched[0]['secret'] == 'vk-test-secret'
