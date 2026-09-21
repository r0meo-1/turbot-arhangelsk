from flask import Flask

from shared.manager_web import manager_web


def test_mobile_shell_serves_only_allowlisted_assets():
    app = Flask(__name__)
    app.register_blueprint(manager_web)
    client = app.test_client()
    for path in ["/manager/", "/manager/app.js", "/manager/styles.css"]:
        response = client.get(path)
        assert response.status_code == 200
        assert response.headers["Cache-Control"] == "no-store"
        assert "connect-src 'self'" in response.headers["Content-Security-Policy"]
        assert "frame-ancestors 'none'" in response.headers["Content-Security-Policy"]
    for path in ["/manager/.env", "/manager/website_app.py", "/manager/../bot.py"]:
        assert client.get(path).status_code == 404


def test_public_shell_contains_no_customer_records():
    app = Flask(__name__)
    app.register_blueprint(manager_web)
    response = app.test_client().get("/manager/")
    assert b'id="desk" hidden' in response.data
    assert b'id="token" type="password"' in response.data
    assert b"/agentdesk" in response.data
