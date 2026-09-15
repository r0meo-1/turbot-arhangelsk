import base64
import hashlib
import hmac
import threading
import time
from urllib.parse import urlencode

import pytest
from flask import Flask
from werkzeug.serving import make_server

playwright_sync = pytest.importorskip("playwright.sync_api", reason="VK Mini App browser E2E runs in Edge Bot CI")
sync_playwright = playwright_sync.sync_playwright

from shared.vk_miniapp import create_blueprint


SECRET = "vk-miniapp-e2e-secret"
APP_ID = "54475121"
GROUP_ID = 240310110
USER_ID = 424242


def _signed_launch_params():
    params = {
        "vk_app_id": APP_ID,
        "vk_group_id": str(GROUP_ID),
        "vk_platform": "desktop_web",
        "vk_ts": str(int(time.time())),
        "vk_user_id": str(USER_ID),
    }
    signed = urlencode(sorted(params.items()))
    params["sign"] = base64.urlsafe_b64encode(
        hmac.new(SECRET.encode(), signed.encode(), hashlib.sha256).digest()
    ).decode().rstrip("=")
    return urlencode(params)


def test_vk_miniapp_browser_roundtrip_to_signed_draft():
    saved = []

    def save_draft(uid, info):
        saved.append((uid, info))

    app = Flask(__name__)
    app.register_blueprint(
        create_blueprint(save_draft, lambda: (SECRET, APP_ID, GROUP_ID))
    )

    server = make_server("127.0.0.1", 0, app)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    try:
        url = f"http://127.0.0.1:{server.server_port}/vk/miniapp/?{_signed_launch_params()}"
        with sync_playwright() as pw:
            browser = pw.chromium.launch(channel="msedge", headless=True)
            page = browser.new_page()
            page.goto(url, wait_until="domcontentloaded")
            page.locator("#destination").fill("Таиланд")
            page.locator("#departure").fill("Архангельск")
            page.locator("#consent").check()
            page.locator("#submit").click()
            page.locator("#review").wait_for(state="visible")
            page.locator("#save").click()
            page.locator("#chat").wait_for(state="visible")

            assert page.locator("#status").inner_text().startswith("Параметры сохранены.")
            assert page.locator("#chat").get_attribute("href") == f"https://vk.ru/im?sel=-{GROUP_ID}"
            browser.close()
    finally:
        server.shutdown()
        thread.join(timeout=5)

    assert len(saved) == 1
    uid, info = saved[0]
    assert uid == USER_ID
    assert info["destination"] == "Таиланд"
    assert info["origin"] == "Архангельск"
    assert info["source"] == "vk_mini_app"
    assert info["budget_scope"] == "total"
