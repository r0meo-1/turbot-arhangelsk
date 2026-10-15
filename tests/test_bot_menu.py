import json

import pytest

import bot


@pytest.mark.parametrize('url', ['https://bot.example.test/tg/miniapp/', ''])
def test_menu_is_serialized_and_updates_before_rate_limited_profile(monkeypatch, url):
    calls = []
    monkeypatch.setattr(bot, 'MINI_APP_URL', url)
    monkeypatch.setattr(bot, 'BOT_TOKEN', 'test')
    monkeypatch.setattr(bot, 'BOT_DISPLAY_NAME', 'Test')
    monkeypatch.setattr(bot._version, 'REVISION', 'abc1234')
    def api(method, payload):
        calls.append((method, payload))
        if method == 'setMyName':
            raise RuntimeError('rate-limited profile must not block menu')
        return True
    monkeypatch.setattr(bot, '_tg_api_ok', api)
    with pytest.raises(RuntimeError):
        bot.ensure_bot_profile()
    assert calls[0][0] == 'setChatMenuButton'
    menu = json.loads(calls[0][1]['menu_button'])
    assert menu['type'] == ('web_app' if url else 'commands')
    if url:
        assert menu['web_app']['url'] == url + '?rev=abc1234'


def test_failed_menu_update_keeps_serialized_commands_fallback(monkeypatch):
    calls = []
    monkeypatch.setattr(bot, 'BOT_TOKEN', 'test')
    for name in ('BOT_DISPLAY_NAME', 'BOT_SHORT_DESCRIPTION', 'BOT_DESCRIPTION'):
        monkeypatch.setattr(bot, name, '')
    def api(method, payload):
        calls.append((method, payload))
        return len(calls) != 1
    monkeypatch.setattr(bot, '_tg_api_ok', api)
    bot.ensure_bot_profile()
    assert calls[1][0] == 'setChatMenuButton'
    assert json.loads(calls[1][1]['menu_button']) == {'type': 'commands'}
