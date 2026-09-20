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


@pytest.mark.parametrize("width", [320, 390, 1280])
@pytest.mark.parametrize("asset_dir", [MINIAPP_DIR, MINIAPP_DIR.parent / "docs" / "miniapp"], ids=["source", "pages"])
def test_telegram_miniapp_browser_reviews_then_posts_v2_payload_and_closes(width, asset_dir):
    handler = partial(_QuietHandler, directory=str(asset_dir))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    captured = []

    try:
        url = f"http://127.0.0.1:{server.server_port}/index.html"
        with sync_playwright() as pw:
            browser = pw.chromium.launch(channel="msedge", headless=True)
            context = browser.new_context(viewport={"width": width, "height": 760})
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

            # Narrow-mobile acceptance: the entire customer form must fit the
            # viewport without page-level horizontal scrolling.
            assert page.evaluate(
                "document.documentElement.scrollWidth <= window.innerWidth"
            )
            shell_box = page.locator(".shell").bounding_box()
            assert shell_box is not None
            assert shell_box["x"] >= 0
            assert shell_box["x"] + shell_box["width"] <= width + 0.5

            # Critical form controls need stable accessible names instead of
            # relying on placeholders or visual proximity.
            for label in (
                "Направление",
                "Вылет",
                "Ночей",
                "Дата вылета",
                "Взрослых",
                "Детей до 18",
                "Бюджет на всю поездку",
            ):
                assert page.get_by_label(label, exact=True).count() == 1

            # Keyboard users must get an explicit visible focus indicator.
            page.locator("#destination").focus()
            assert page.locator("#destination").evaluate(
                "el => getComputedStyle(el).outlineStyle !== 'none' && getComputedStyle(el).outlineWidth !== '0px'"
            )
            page.keyboard.press("Tab")
            focused = page.evaluate(
                """() => {
                  const el = document.activeElement;
                  const style = getComputedStyle(el);
                  return {
                    isChip: el?.classList?.contains('chip') || false,
                    outlineWidth: style.outlineWidth,
                    outlineStyle: style.outlineStyle
                  };
                }"""
            )
            assert focused["isChip"] is True
            assert focused["outlineStyle"] != "none"
            assert focused["outlineWidth"] != "0px"

            # start_param=thailand should prefill the country, then the user may
            # choose a more specific resort suggestion.
            assert page.locator("#destination").input_value() == "Таиланд"
            destination_options = page.locator("#destination-options option").evaluate_all(
                "els => els.map((el) => el.value)"
            )
            departure_options = page.locator("#departure-cities option").evaluate_all(
                "els => els.map((el) => el.value)"
            )
            assert "Пхукет, Таиланд" in destination_options
            assert "Нячанг, Вьетнам" in destination_options
            assert "Архангельск" in departure_options
            assert "Москва" in departure_options
            page.locator("#destination").fill("Пхукет, Таиланд")
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
            assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
            assert captured == []
            assert page.evaluate("window.__tgClosed") is False
            assert page.evaluate("window.__tgBackVisible") is True
            review_text = page.locator("#summary").inner_text().replace("\u00a0", " ")
            for expected in (
                "Пхукет, Таиланд", "Архангельск", "10", "2", "5", "270 000", "Бюджет на всех", "только прямой"
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
        "destination": "Пхукет, Таиланд",
        "departure": "Архангельск",
        "date": payload["date"],
        "nights": 10,
        "adults": 2,
        "children": 1,
        "childrenAges": [5],
        "budgetMaxRub": 270000,
        "budgetScope": "total",
        "directOnly": True,
        "consent": True,
        "source": "telegram_mini_app",
    }
    assert payload["date"]
