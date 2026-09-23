import json
import threading
from http.server import BaseHTTPRequestHandler
from pathlib import Path

import pytest
from flask import Flask
from werkzeug.serving import make_server

from shared.manager_web import manager_web

playwright = pytest.importorskip("playwright.sync_api")


FIXTURE = Path(__file__).parent / "fixtures" / "manager_demo.json"


class _Server:
    def __init__(self):
        app = Flask(__name__)
        app.register_blueprint(manager_web)
        self.server = make_server("127.0.0.1", 0, app)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def url(self):
        return f"http://127.0.0.1:{self.server.server_port}/manager/"

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *_):
        self.server.shutdown()
        self.thread.join(timeout=2)


def test_mobile_demo_is_deterministic_and_has_no_crm_write_requests():
    expected = json.loads(FIXTURE.read_text(encoding="utf-8"))
    with _Server() as local:
        with playwright.sync_playwright() as pw:
            browser = pw.chromium.launch(channel="msedge", headless=True)
            page = browser.new_page(viewport={"width": 390, "height": 844}, is_mobile=True)
            crm_requests = []
            page.on("request", lambda request: crm_requests.append(request) if "/agent-extension/crm/" in request.url else None)
            page.goto(local.url, wait_until="networkidle")
            assert page.locator("#token").input_value() == ""
            page.locator("#demo").click()
            assert page.locator("#status").inner_text() == "Демо-режим: синтетические данные, CRM не подключена."
            assert expected["task_text"] in page.locator("#tasks").inner_text()
            page.locator("#tasks button").click()
            assert expected["summary"] in page.locator("#summary").inner_text()
            assert expected["quote"] in page.locator("#quotes").inner_text()
            assert expected["activity"] in page.locator("#activities").inner_text()
            page.locator("#note").fill("локальная проверка")
            page.locator("#saveNote").click()
            assert "только для просмотра" in page.locator("#status").inner_text()
            assert crm_requests == []
            browser.close()
