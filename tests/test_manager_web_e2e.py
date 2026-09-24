import json
from pathlib import Path

import pytest
from flask import Flask
from werkzeug.serving import make_server

from shared.manager_web import manager_web
from e2e_support import QuietRequestHandler, isolated_browser, running_server

playwright = pytest.importorskip("playwright.sync_api")


FIXTURE = Path(__file__).parent / "fixtures" / "manager_demo.json"


def test_mobile_demo_is_deterministic_and_has_no_crm_write_requests():
    expected = json.loads(FIXTURE.read_text(encoding="utf-8"))
    app = Flask(__name__)
    app.register_blueprint(manager_web)
    with running_server(make_server("127.0.0.1", 0, app, request_handler=QuietRequestHandler)) as server:
        with playwright.sync_playwright() as pw, isolated_browser(
            pw, simulated_online=True, viewport={"width": 390, "height": 844}, is_mobile=True
        ) as context:
            page = context.new_page()
            crm_requests = []
            page.on("request", lambda request: crm_requests.append(request) if "/agent-extension/crm/" in request.url else None)
            page.goto(f"http://127.0.0.1:{server.server_port}/manager/", wait_until="networkidle")
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
