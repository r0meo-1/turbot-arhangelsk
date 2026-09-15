import json
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

playwright_sync = pytest.importorskip(
    "playwright.sync_api", reason="Telegram Mini App browser E2E runs in Edge Bot CI"
)
sync_playwright = playwright_sync.sync_playwright


MINIAPP_DIR = Path(__file__).resolve().parents[1] / "miniapp"
API_URL = "https://bot.r0meo1.ru/miniapp/submit"
INIT_DATA = "query_id=e2e&auth_date=2099999999&hash=e2e-placeholder"


class _QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, format, *args):
        return


def test_telegram_miniapp_browser_reviews_then_posts_v2_payload_and_closes():
    handler = partial(_QuietHandler, directory=str(MINIAPP_DIR))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    captured = []

    try:
        url = f"http://127.0.0.1:{server.server_port}/index.html"
        with sync_playwright() as pw:
            browser = pw.chromium.launch(channel="msedge", headless=True)
            context = browser.new_context()
            context.add_init_script(
                f"""
                window.__tgClosed = false;
                window.__tgBackVisible = false;
                window.__tgBackHandler = null;
                window.Telegram = {{
                  WebApp: {{
                    initData: {json.dumps(INIT_DATA)},
                    initDataUnsafe: {{
                      user: {{ id: 88001, first_name: 'Roma', username: 'tester' }},
                      start_param: 'thailand'
                    }},
                    ready() {{}},
                    expand() {{}},
                    setHeaderColor() {{}},
                    setBackgroundColor() {{}},
                    close() {{ window.__tgClosed = true; }},
                    sendData(data) {{ window.__tgSendData = data; }},
                    BackButton: {{
                      show() {{ window.__tgBackVisible = true; }},
                      hide() {{ window.__tgBackVisible = false; }},
                      onClick(handler) {{ window.__tgBackHandler = handler; }}
                    }},
                    HapticFeedback: {{
                      selectionChanged() {{}},
                      notificationOccurred() {{}}
                    }}
                  }}
                }};
                """
            )
            page = context.new_page()

            # Keep the test deterministic and independent from telegram.org.
            page.route("https://telegram.org/js/telegram-web-app.js", lambda route: route.abort())

            def accept_submit(route, request):
                captured.append(request.post_data_json)
                route.fulfill(
                    status=200,
                    content_type="application/json",
                    body='{"ok":true}',
                    headers={"Access-Control-Allow-Origin": "*"},
                )

            page.route(API_URL, accept_submit)
            page.goto(url, wait_until="domcontentloaded")

            # start_param=thailand should prefill the destination.
            assert page.locator("#destination").input_value() == "Таиланд"
            page.locator("#departure").fill("Архангельск")
            page.locator("#nights").fill("10")
            page.locator("#adults").fill("2")
            page.locator("#children").fill("1")
            page.locator("#children-ages input").fill("5")
            page.locator("#budget").evaluate(
                "el => { el.value = '270000'; el.dispatchEvent(new Event('input', {bubbles:true})); }"
            )
            page.locator("#direct").check()
            page.locator("#consent").check()
            page.locator("#submit").click()

            # Review is local only: no backend write and no WebView close yet.
            page.locator("#review").wait_for(state="visible")
            assert captured == []
            assert page.evaluate("window.__tgClosed") is False
            assert page.evaluate("window.__tgBackVisible") is True
            review_text = page.locator("#summary").inner_text()
            for expected in (
                "Таиланд", "Архангельск", "10", "2", "5", "270 000", "только прямой"
            ):
                assert expected in review_text

            # Edit must return to the form without discarding entered values.
            page.locator("#edit").click()
            page.locator("#trip-form").wait_for(state="visible")
            assert page.locator("#departure").input_value() == "Архангельск"
            assert page.locator("#children-ages input").input_value() == "5"
            assert page.evaluate("window.__tgBackVisible") is False

            # Re-open review and explicitly save.
            page.locator("#submit").click()
            page.locator("#review").wait_for(state="visible")
            page.locator("#save").click()
            page.wait_for_function("window.__tgClosed === true")
            browser.close()
    finally:
        server.shutdown()
        thread.join(timeout=5)

    assert len(captured) == 1
    request_body = captured[0]
    assert request_body["initData"] == INIT_DATA
    payload = request_body["payload"]
    assert payload == {
        "type": "trip_request",
        "version": 2,
        "destination": "Таиланд",
        "departure": "Архангельск",
        "date": payload["date"],
        "nights": 10,
        "adults": 2,
        "children": 1,
        "childrenAges": [5],
        "budgetMaxRub": 270000,
        "directOnly": True,
        "consent": True,
        "source": "telegram_mini_app",
    }
    assert payload["date"]
