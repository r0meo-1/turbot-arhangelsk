import base64
import hashlib
import hmac
import logging
import time
from datetime import date, timedelta
from urllib.parse import urlencode

import pytest
from flask import Flask
from shared.vk_miniapp import create_blueprint, validate_launch_params, validate_vk_trip
from shared.telegram_webapp import MiniAppValidationError

SECRET = 'test-app-secret'


def signed(**changes):
    params = dict(vk_app_id='123', vk_group_id='999', vk_user_id='42', vk_ts=str(int(time.time())))
    params.update(changes)
    query = urlencode(sorted(params.items()))
    signature = base64.urlsafe_b64encode(hmac.new(SECRET.encode(), query.encode(), hashlib.sha256).digest()).decode().rstrip('=')
    return query + '&sign=' + signature


def payload():
    return dict(type='trip_request', version=2, destination='Египет', departure='Архангельск',
                date=(date.today() + timedelta(days=30)).isoformat(), nights=10,
                adults=2, children=2, childrenAges=[0, 14], budgetMaxRub=270000, consent=True, termsAccepted=True)


def test_signature_identity():
    assert validate_launch_params(signed(), SECRET, '123', 999) == 42
    assert validate_launch_params(signed(vk_group_id='0'), SECRET, '123', 999) == 42


@pytest.mark.parametrize('case', [
    'tampered_user', 'wrong_app', 'wrong_group', 'zero_user',
    'expired', 'future', 'duplicate_user', 'none', 'non_ascii_sign',
])
def test_reject_auth(case):
    now = 1_800_000_000
    valid = signed(vk_ts=str(now))
    cases = {
        'tampered_user': valid.replace('vk_user_id=42', 'vk_user_id=43'),
        'wrong_app': signed(vk_app_id='321', vk_ts=str(now)),
        'wrong_group': signed(vk_group_id='1000', vk_ts=str(now)),
        'zero_user': signed(vk_user_id='0', vk_ts=str(now)),
        'expired': signed(vk_ts=str(now - 3601)),
        'future': signed(vk_ts=str(now + 61)),
        'duplicate_user': valid + '&vk_user_id=42',
        'none': None,
        'non_ascii_sign': 'sign=é',
    }
    with pytest.raises(MiniAppValidationError):
        validate_launch_params(cases[case], SECRET, '123', 999, now=now)


@pytest.mark.parametrize('change', [dict(consent=False), dict(termsAccepted=False), dict(nights=4.5), dict(childrenAges=[True, 14]),
    dict(childrenAges=[18, 2]), dict(childrenAges=[2]), dict(adults=True), dict(budgetMaxRub=999),
    dict(date='2000-01-01'), dict(destination='  ')])
def test_reject_trip(change):
    with pytest.raises(MiniAppValidationError):
        validate_vk_trip(dict(payload(), **change))


def test_draft_api_and_static():
    saved = []
    app = Flask(__name__)
    app.register_blueprint(create_blueprint(lambda uid, info: saved.append((uid, info)), lambda: (SECRET, '123', 999)))
    client = app.test_client()
    assert client.post('/vk/miniapp/draft', json={}).status_code == 401
    assert client.post('/vk/miniapp/draft', json={'launchParams': signed(), 'payload': {}}).status_code == 400
    assert not saved
    response = client.post('/vk/miniapp/draft', json={'launchParams': signed(), 'payload': payload()})
    assert response.json == {'ok': True, 'state': 'review', 'groupId': 999}
    assert saved[0][0] == 42
    assert saved[0][1]['budget_scope'] == 'total'
    assert saved[0][1]['source'] == 'vk_mini_app'
    assert response.headers['Cache-Control'] == 'no-store'
    for path in (
        '', 'app.js', 'styles.css', 'vk-bridge.js', 'legal.js',
        'privacy.html', 'consent.html', 'terms.html', 'moderation.html',
    ):
        assert client.get('/vk/miniapp/' + path).status_code == 200
    legal = client.get('/vk/miniapp/legal.json')
    assert legal.status_code == 200
    assert legal.json['operatorName'] == 'ИП Замятина Мария Андреевна, ОГРНИП 311293232600026'
    assert legal.json['operatorName'] != 'ТА «АПРЕЛЬ тур»'
    assert legal.json['projectUrl'] == 'https://r0meo1.ru/apreltour/'
    privacy = client.get('/vk/miniapp/privacy.html').get_data(as_text=True)
    assert 'ЧЕРНОВИК' not in privacy
    assert 'Telegram' not in privacy
    assert 'Политика обработки персональных данных' in privacy
    assert 'Рекламные сообщения' in privacy
    assert 'https://r0meo1.ru/apreltour/' not in privacy
    consent = client.get('/vk/miniapp/consent.html').get_data(as_text=True)
    terms = client.get('/vk/miniapp/terms.html').get_data(as_text=True)
    moderation = client.get('/vk/miniapp/moderation.html').get_data(as_text=True)
    assert 'Согласие на обработку персональных данных' in consent
    assert 'Условия использования VK Mini App' in terms
    assert 'Правила модерации и безопасного использования' in moderation
    for legal_page in (privacy, consent, terms, moderation):
        assert 'https://r0meo1.ru/apreltour/' not in legal_page
    assert client.get('/vk/miniapp/README.md').status_code == 404


def test_draft_diagnostics_never_log_launch_query_secret_or_user_id(caplog):
    saved = []
    app = Flask(__name__)
    app.logger.setLevel(logging.INFO)
    app.register_blueprint(create_blueprint(lambda uid, info: saved.append((uid, info)), lambda: (SECRET, '123', 999)))
    client = app.test_client()
    launch = signed(vk_ref='community_messages', vk_platform='desktop_web')

    with caplog.at_level(logging.INFO):
        response = client.post('/vk/miniapp/draft', json={'launchParams': launch, 'payload': payload()})

    assert response.status_code == 200
    assert 'vk_miniapp_draft status=saved ref=community_messages platform=desktop_web' in caplog.text
    for forbidden in (launch, 'vk_user_id=42', 'sign=', SECRET):
        assert forbidden not in caplog.text

    caplog.clear()
    tampered = launch.replace('vk_user_id=42', 'vk_user_id=43')
    with caplog.at_level(logging.INFO):
        response = client.post('/vk/miniapp/draft', json={'launchParams': tampered, 'payload': payload()})

    assert response.status_code == 401
    assert 'vk_miniapp_draft status=auth_rejected reason=Invalid signature' in caplog.text
    for forbidden in (tampered, 'vk_user_id=43', 'sign=', SECRET):
        assert forbidden not in caplog.text


def test_unconfigured_is_closed():
    app = Flask(__name__)
    app.register_blueprint(create_blueprint(lambda *a: pytest.fail('must not save'), lambda: ('', '', 999)))
    assert app.test_client().post('/vk/miniapp/draft', json={}).status_code == 503


def test_save_failure_does_not_report_success():
    def fail(*args):
        raise RuntimeError('database offline')
    app = Flask(__name__)
    app.register_blueprint(create_blueprint(fail, lambda: (SECRET, '123', 999)))
    response = app.test_client().post('/vk/miniapp/draft', json={'launchParams': signed(), 'payload': payload()})
    assert response.status_code == 500 and response.json['ok'] is False


def test_legal_config_uses_verified_fallback_and_allows_override(monkeypatch):
    monkeypatch.delenv("DATA_OPERATOR_NAME", raising=False)
    app = Flask(__name__)
    app.register_blueprint(create_blueprint(lambda *_: None, lambda: (SECRET, "123", 999)))
    client = app.test_client()
    legal = client.get("/vk/miniapp/legal.json")
    assert legal.status_code == 200
    assert legal.json["operatorName"] == (
        "ИП Замятина Мария Андреевна, ОГРНИП 311293232600026"
    )

    monkeypatch.setenv("DATA_OPERATOR_NAME", "ИП Проверенный Оператор")
    overridden = client.get("/vk/miniapp/legal.json")
    assert overridden.json["operatorName"] == "ИП Проверенный Оператор"


def test_vk_blueprint_security_headers():
    app = Flask(__name__)
    app.register_blueprint(create_blueprint(lambda *_: None, lambda: (SECRET, "123", 999)))
    response = app.test_client().get("/vk/miniapp/legal.json")
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["Referrer-Policy"] == "no-referrer"
    assert response.headers["Cache-Control"] == "no-store"
    assert "camera=()" in response.headers["Permissions-Policy"]
    assert response.headers["Strict-Transport-Security"] == "max-age=31536000"
    csp = response.headers["Content-Security-Policy-Report-Only"]
    assert "default-src 'self'" in csp
    assert "object-src 'none'" in csp
    assert "frame-ancestors" not in csp
