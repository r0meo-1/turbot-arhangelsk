"""The shared requests session must proxy Telegram without rerouting suppliers."""
import pytest
import requests

import bot


@pytest.mark.parametrize('url,expected_proxy', [
    ('https://api.telegram.org/botTEST/getUpdates', True),
    ('https://api.telegram.org/botTEST/sendMessage', True),
    ('https://api.vk.com/method/messages.send', False),
    ('https://sapi.alean.ru:3443/services', False),
    ('https://crm.example.test/api', False),
    ('https://api.telegram.org.evil.test/', False),
    ('https://api.telegram.org@evil.test/', False),
])
def test_proxy_applies_only_to_telegram(monkeypatch, url, expected_proxy):
    proxy_url = 'http://test-user:test-password@127.0.0.1:8888'
    monkeypatch.setenv('TELEGRAM_PROXY_URL', proxy_url)
    captured = {}

    def capture_send(self, request, **kwargs):
        captured.update(kwargs)
        response = requests.Response()
        response.status_code = 200
        response._content = b'{"ok":true}'
        response.request = request
        return response

    monkeypatch.setattr(requests.adapters.HTTPAdapter, 'send', capture_send)
    session = bot._create_telegram_session()
    session.trust_env = False
    session.get(url)
    assert (captured.get('proxies', {}).get('https') == proxy_url) is expected_proxy
    assert captured['verify'] is True


@pytest.mark.parametrize('value', ['', '   '])
def test_empty_proxy_preserves_default_adapter(monkeypatch, value):
    monkeypatch.setenv('TELEGRAM_PROXY_URL', value)
    session = bot._create_telegram_session()
    adapter = session.get_adapter('https://api.telegram.org/botTEST/getMe')
    assert type(adapter) is requests.adapters.HTTPAdapter
    assert adapter.max_retries.total == 3
