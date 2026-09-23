from flask import Flask

from shared.manager_web import manager_web


def test_mobile_shell_serves_only_allowlisted_assets():
    app = Flask(__name__)
    app.register_blueprint(manager_web)
    client = app.test_client()
    for path in ["/manager/", "/manager/app.js", "/manager/demo-fixture.js", "/manager/styles.css"]:
        response = client.get(path)
        assert response.status_code == 200
        assert response.headers["Cache-Control"] == "no-store"
        assert "connect-src 'self'" in response.headers["Content-Security-Policy"]
        assert "script-src 'self' https://telegram.org" in response.headers["Content-Security-Policy"]
        assert "frame-ancestors 'none'" in response.headers["Content-Security-Policy"]
    for path in ["/manager/.env", "/manager/owner-session.js", "/manager/live-validation.js", "/manager/website_app.py", "/manager/../bot.py"]:
        assert client.get(path).status_code == 404


def test_public_shell_contains_no_customer_records():
    app = Flask(__name__)
    app.register_blueprint(manager_web)
    response = app.test_client().get("/manager/")
    assert b'id="desk" hidden' in response.data
    assert b'id="token" type="password"' in response.data
    assert b"/agentdesk" in response.data
    assert b"https://telegram.org/js/telegram-web-app.js" in response.data
    assert b'id="analytics"' in response.data
    assert b'id="replyTemplate"' in response.data
    assert "время системной доставки менеджеру" in response.get_data(as_text=True)
    assert "Кнопка только копирует текст" in response.get_data(as_text=True)


def test_client_bundle_never_contains_server_only_secret_names():
    app = Flask(__name__)
    app.register_blueprint(manager_web)
    client = app.test_client()
    for path in ["/manager/", "/manager/app.js", "/manager/demo-fixture.js"]:
        body = client.get(path).data
        for forbidden in [b"BOT_TOKEN", b"ADMIN_ID", b"AGENT_EXTENSION_TOKEN"]:
            assert forbidden not in body


def test_client_uses_signed_telegram_header_without_persisting_it():
    app = Flask(__name__)
    app.register_blueprint(manager_web)
    body = app.test_client().get("/manager/app.js").data
    assert b"X-Telegram-Init-Data" in body
    assert b"localStorage" not in body
    assert b"sessionStorage" not in body
    assert b"api('summary')" in body
    assert b"api('assign'" in body
    assert b"api('unassign'" in body
    assert "Взять в работу" in body.decode("utf-8")
    assert "Освободить заявку" in body.decode("utf-8")
    assert b"navigator.clipboard.writeText" in body
    assert b"api.telegram.org" not in body
    assert b"sendMessage" not in body
    assert b"window.open" not in body
