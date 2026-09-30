"""Delayed assignment responses must stay attached to their original card."""
import json
from contextlib import ExitStack
from urllib.parse import parse_qs, urlsplit

import pytest
from flask import Flask
from werkzeug.serving import make_server

from e2e_resources import local_server
from shared.manager_web import manager_web

playwright = pytest.importorskip("playwright.sync_api")


@pytest.mark.parametrize("operation", ["assign", "unassign"])
@pytest.mark.parametrize("navigation", ["card", "refresh", "logout", "stay"])
@pytest.mark.parametrize("response_status", [200, 409])
def test_delayed_assignment_cannot_overwrite_new_view(operation, navigation, response_status):
    app = Flask(__name__)
    app.register_blueprint(manager_web)
    pending = []

    def crm(route):
        url = urlsplit(route.request.url)
        action = url.path.rsplit("/", 1)[-1]
        if action in ("assign", "unassign"):
            pending.append(route)
            return
        if action == "summary":
            body = {"ok": True, "summary": {}}
        elif action == "today":
            body = {"ok": True, "tasks": [
                {"requestId": request_id, "type": "next_contact", "destination": request_id}
                for request_id in ("lead-a", "lead-b")
            ]}
        elif action == "timeline":
            request_id = parse_qs(url.query)["requestId"][0]
            assignment = {"assigned": False}
            if request_id == "lead-b":
                assignment = {"assigned": True, "name": "Другой менеджер", "canRelease": False}
            elif operation == "unassign":
                assignment = {"assigned": True, "name": "Первый менеджер", "canRelease": True}
            body = {"ok": True, "timeline": {
                "request": {"primary_destination": request_id}, "assignment": assignment,
            }}
        else:
            raise AssertionError(f"Unexpected synthetic CRM request: {action}")
        route.fulfill(content_type="application/json", body=json.dumps(body))

    with local_server(make_server("127.0.0.1", 0, app)) as server:
        with playwright.sync_playwright() as pw, ExitStack() as resources:
            browser = pw.chromium.launch(channel="msedge", headless=True)
            resources.callback(browser.close)
            page = browser.new_page(viewport={"width": 390, "height": 844})
            page.route("https://telegram.org/js/telegram-web-app.js", lambda route: route.abort())
            page.route("**/agent-extension/crm/**", crm)
            page.goto(f"http://127.0.0.1:{server.server_port}/manager/")
            page.locator("#token").fill("synthetic-manager-test")
            page.locator("#connect").click()
            playwright.expect(page.locator("#status")).to_have_text("Очередь обновлена.")
            page.locator("#tasks button").nth(0).click()
            playwright.expect(page.locator("#summary")).to_have_text("lead-a")
            mutation_button = page.get_by_role("button", name=(
                "Взять в работу" if operation == "assign" else "Освободить заявку"
            ), exact=True)
            original_button = mutation_button.element_handle()
            mutation_button.click()
            playwright.expect(page.locator("#status")).to_have_text(
                "Назначение…" if operation == "assign" else "Освобождение…"
            )
            if navigation == "card":
                page.locator("#tasks button").nth(1).click()
                playwright.expect(page.locator("#summary")).to_have_text("lead-b")
            elif navigation != "stay":
                page.locator("#" + navigation).click()
                playwright.expect(page.locator("#status")).to_have_text(
                    "Очередь обновлена." if navigation == "refresh" else "Вы вышли."
                )
            expected_status = page.locator("#status").inner_text()
            expected_assignment = page.locator("#assignment").inner_text()
            assert len(pending) == 1
            assert json.loads(pending[0].request.post_data)["requestId"] == "lead-a"
            result_assignment = (
                {"assigned": True, "name": "Запоздалый менеджер"}
                if operation == "assign" else {"assigned": False}
            )
            pending[0].fulfill(status=response_status, content_type="application/json", body=json.dumps(
                {"ok": True, "assignment": result_assignment}
                if response_status == 200 else {"ok": False, "error": "assignment_changed"}
            ))
            # finally re-enables the original button, even after detachment.
            page.wait_for_function("button => !button.disabled", arg=original_button)
            if navigation == "stay":
                if response_status == 409:
                    playwright.expect(page.locator("#status")).to_have_text(
                        "Назначение уже изменилось. Обновите карточку."
                    )
                    assert page.locator("#assignment").inner_text() == expected_assignment
                else:
                    playwright.expect(page.locator("#status")).to_have_text(
                        "Заявка назначена вам." if operation == "assign"
                        else "Назначение снято. Другой менеджер сможет взять заявку."
                    )
                    playwright.expect(page.locator("#assignment")).to_contain_text(
                        "Ответственный: Запоздалый менеджер" if operation == "assign"
                        else "Ответственный ещё не назначен."
                    )
                return
            assert page.locator("#status").inner_text() == expected_status
            assert page.locator("#assignment").inner_text() == expected_assignment
            if navigation == "logout":
                playwright.expect(page.locator("#desk")).to_be_hidden()
