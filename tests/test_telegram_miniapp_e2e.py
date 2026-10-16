import json
from contextlib import ExitStack
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from e2e_resources import local_server

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


def test_same_origin_save_works_with_cross_origin_api_blocked_and_retry_is_stable():
    with sync_playwright() as pw, ExitStack() as resources:
        browser = pw.chromium.launch(channel='msedge', headless=True)
        resources.callback(browser.close)
        page = browser.new_page(viewport={'width': 390, 'height': 850})
        page.add_init_script("""window.__nativeEvents = [];
            window.TelegramWebviewProxy = {postEvent(type, data) { window.__nativeEvents.push(type); }};""")
        # Reproduce the blocked cross-domain request without weakening auth or TLS.
        page.route(API_URL, lambda route: route.abort())
        attempts = []
        origin = 'https://miniapp.example.test'
        from urllib.parse import urlsplit
        def asset(route):
            name = urlsplit(route.request.url).path.removeprefix('/tg/miniapp/') or 'index.html'
            assert name in ('index.html', 'app.js', 'styles.css', 'vendor/telegram-web-app.js', 'departure-support.js')
            route.fulfill(path=MINIAPP_DIR / name)
        page.route(origin + '/tg/miniapp/**', asset)
        def submit(route):
            attempts.append(route.request.post_data_json)
            assert route.request.headers.get('origin') == origin
            if len(attempts) == 1:
                route.abort()
            else:
                route.fulfill(status=200, content_type='application/json', body='{"ok":true}')
        page.route(origin + '/miniapp/submit', submit)
        from urllib.parse import urlencode
        fragment = urlencode({'tgWebAppData': INIT_DATA, 'tgWebAppVersion': '8.0', 'tgWebAppPlatform': 'tdesktop'})
        page.goto(origin + '/tg/miniapp/#' + fragment, wait_until='domcontentloaded')
        page.locator('#trip-form[data-ready="true"]').wait_for()
        page.locator('#destination').fill('Сочи, Россия')
        page.locator('#budget-amount').fill('270123')
        page.locator('#consent').check()
        page.locator('#submit').click()
        assert page.locator('#review').is_visible(), page.locator('#error').inner_text()
        page.locator('#save').click()
        page.wait_for_function("document.getElementById('save').disabled === false")
        assert 'Не удалось подтвердить сохранение.' in page.locator('#status').inner_text(), (attempts, page.locator('#status').inner_text())
        assert 'Failed to fetch' not in page.locator('#status').inner_text()
        assert not page.evaluate("window.__nativeEvents.includes('web_app_close')")
        page.locator('#save').click()
        page.wait_for_function("window.__nativeEvents.includes('web_app_close')")
        assert len(attempts) == 2 and attempts[0] == attempts[1]
        assert attempts[0]['payload']['budgetMaxRub'] == 270123
        assert attempts[0]['initData'] == INIT_DATA


@pytest.mark.parametrize("asset_dir", [MINIAPP_DIR, MINIAPP_DIR.parent / "docs" / "miniapp"], ids=["source", "pages"])
@pytest.mark.parametrize('flexible', [False, True])
def test_real_sdk_loads_without_telegram_org_and_preserves_native_init_data(asset_dir, flexible):
    from urllib.parse import urlencode

    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(_QuietHandler, directory=str(asset_dir)))
    with local_server(server), sync_playwright() as pw, ExitStack() as resources:
        browser = pw.chromium.launch(channel="msedge", headless=True)
        resources.callback(browser.close)
        page = browser.new_page(viewport={"width": 390, "height": 850})
        page.add_init_script("""window.__nativeEvents = [];
            window.TelegramWebviewProxy = {postEvent(type, data) { window.__nativeEvents.push(type); }};""")
        external, submissions = [], []
        page.route("https://telegram.org/**", lambda route: (external.append(route.request.url), route.abort()))
        page.route(API_URL, lambda route: (submissions.append(route.request.post_data_json), route.fulfill(status=200, content_type="application/json", body='{"ok":true}', headers={"Access-Control-Allow-Origin": "*"})))
        fragment = urlencode({"tgWebAppData": INIT_DATA, "tgWebAppVersion": "8.0", "tgWebAppPlatform": "tdesktop"})
        page.goto(f"http://127.0.0.1:{server.server_port}/index.html#{fragment}", wait_until="domcontentloaded")
        page.locator('#trip-form[data-ready="true"]').wait_for()
        assert page.evaluate("Telegram.WebApp.initData") == INIT_DATA
        page.locator("#destination").fill("Сочи, Россия")
        if flexible:
            from datetime import date, timedelta
            last_date = (date.fromisoformat(page.locator('#date').input_value()) + timedelta(days=10)).isoformat()
            page.locator('#nights-mode').select_option('range')
            page.locator('#nights').fill('7')
            page.locator('#nights-to').fill('10')
            page.locator('#date-mode').select_option('range')
            page.locator('#date-to').fill(last_date)
        page.locator("#consent").check()
        page.locator("#submit").click()
        page.locator("#review").wait_for(state="visible")
        assert page.locator("#review").evaluate("el => el.getBoundingClientRect().top < document.querySelector('[aria-labelledby=apreltour-site-title]').getBoundingClientRect().top")
        assert submissions == []
        if flexible:
            assert last_date in page.locator('#summary').inner_text()
            page.evaluate("Telegram.WebView.receiveEvent('back_button_pressed')")
            page.locator('#trip-form').wait_for(state='visible')
            assert page.locator('#date-to').input_value() == last_date
            page.locator('#date').fill('2099-12-01')
            assert page.locator('#date-to').get_attribute('min') == '2099-12-01'
            assert page.locator('#date-to').get_attribute('max') == '2099-12-31'
            last_date = '2099-12-11'
            page.locator('#nights-mode').select_option('range')
            page.locator('#nights').fill('7')
            page.locator('#nights-to').fill('10')
            page.locator('#date-mode').select_option('range')
            page.locator('#date-to').fill(last_date)
            page.locator('#submit').click()
        page.locator("#save").click()
        page.wait_for_function("window.__nativeEvents.includes('web_app_close')")
        assert len(submissions) == 1
        assert submissions[0]["initData"] == INIT_DATA
        if flexible:
            assert submissions[0]['payload']['dateTo'] == last_date
            assert submissions[0]['payload']['nights'] == 7 and submissions[0]['payload']['nightsTo'] == 10
        assert external == []


@pytest.mark.parametrize("asset_dir", [MINIAPP_DIR, MINIAPP_DIR.parent / "docs" / "miniapp"], ids=["source", "pages"])
def test_telegram_form_cannot_submit_before_handlers_load(asset_dir):
    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(_QuietHandler, directory=str(asset_dir)))
    with local_server(server), sync_playwright() as pw, ExitStack() as resources:
        browser = pw.chromium.launch(channel="msedge", headless=True)
        resources.callback(browser.close)
        page = browser.new_page()
        page.route("**/vendor/telegram-web-app.js*", lambda route: route.abort())
        scripts, navigations, submissions = [], [], []
        page.route("**/app.js*", lambda route: scripts.append(route))
        page.route(API_URL, lambda route: (submissions.append(route.request.url), route.abort()))
        page.on("request", lambda request: navigations.append(request.url) if request.is_navigation_request() else None)
        url = f"http://127.0.0.1:{server.server_port}/index.html"
        with page.expect_request("**/app.js*"):
            page.goto(url, wait_until="commit")
        playwright_sync.expect(page.locator("#submit")).to_be_disabled()
        page.locator("#destination").fill("QA LOCAL ONLY")
        page.locator("#destination").press("Enter")
        assert navigations == [url]
        assert submissions == []
        assert len(scripts) == 1
        scripts[0].fulfill(content_type="application/javascript", body=(asset_dir / "app.js").read_text(encoding="utf-8"))
        page.locator('#trip-form[data-ready="true"]').wait_for()
        playwright_sync.expect(page.locator("#submit")).to_be_enabled()



def _assert_zoom_and_default_contrast(page):
    viewport = page.locator('meta[name="viewport"]').get_attribute("content") or ""
    normalized = viewport.lower().replace(" ", "")
    assert "user-scalable=no" not in normalized
    assert "maximum-scale=1" not in normalized

    ratios = page.evaluate(
        r"""() => {
          const rgb = (value) => {
            const match = value.match(/rgba?\((\d+),\s*(\d+),\s*(\d+)/);
            if (!match) throw new Error('unsupported color: ' + value);
            return match.slice(1, 4).map(Number);
          };
          const luminance = (value) => {
            const [r, g, b] = rgb(value).map((v) => {
              const c = v / 255;
              return c <= 0.04045 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4);
            });
            return 0.2126 * r + 0.7152 * g + 0.0722 * b;
          };
          const ratio = (fg, bg) => {
            const a = luminance(fg);
            const b = luminance(bg);
            return (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05);
          };
          const body = getComputedStyle(document.body);
          const welcome = getComputedStyle(document.querySelector('#welcome'));
          return {
            body: ratio(body.color, body.backgroundColor),
            muted: ratio(welcome.color, body.backgroundColor)
          };
        }"""
    )
    assert ratios["body"] >= 4.5
    assert ratios["muted"] >= 4.5


@pytest.mark.parametrize("width", [320, 390])
@pytest.mark.parametrize("asset_dir", [MINIAPP_DIR, MINIAPP_DIR.parent / "docs" / "miniapp"], ids=["source", "pages"])
def test_telegram_budget_back_and_cancel_never_submit(width, asset_dir):
    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(_QuietHandler, directory=str(asset_dir)))
    with local_server(server), sync_playwright() as pw, ExitStack() as resources:
        browser = pw.chromium.launch(channel="msedge", headless=True)
        resources.callback(browser.close)
        page = browser.new_page(viewport={"width": width, "height": 850})
        page.route("**/vendor/telegram-web-app.js*", lambda route: route.abort())
        submissions = []
        page.route(API_URL, lambda route: (submissions.append(route.request.url), route.abort()))
        page.goto(f"http://127.0.0.1:{server.server_port}/index.html")
        page.locator('#trip-form[data-ready="true"]').wait_for()
        page.get_by_role("button", name="Сочи", exact=True).click()
        page.locator("#budget-amount").fill("270123")
        assert page.locator("#budget").input_value() == "270123"
        assert "270" in page.locator("#budget-output").inner_text()
        page.locator("#children").fill("1")
        page.locator("#children-ages input").fill("0")
        page.locator("#consent").check()
        page.locator("#submit").click()
        page.locator("#review").wait_for(state="visible")
        assert "Сочи, Россия" in page.locator("#summary").inner_text()
        assert "270 123" in page.locator("#summary").inner_text().replace("\u00a0", " ")
        assert "0 лет" in page.locator("#summary").inner_text()
        page.locator("#edit").click()
        assert page.locator("#budget-amount").input_value() == "270123"
        assert page.locator("#children-ages input").input_value() == "0"
        page.get_by_role("button", name="Очистить параметры", exact=True).click()
        playwright_sync.expect(page.locator("#keep-trip")).to_be_focused()
        page.locator("#keep-trip").click()
        assert page.locator("#destination").input_value() == "Сочи, Россия"
        page.locator("#budget-amount").fill("99999")
        page.locator("#submit").click()
        assert page.locator("#review").is_hidden()
        page.locator("#budget-amount").fill("350001")
        page.locator("#submit").click()
        page.locator("#review").wait_for(state="visible")
        page.get_by_role("button", name="Отменить подбор", exact=True).click()
        page.locator("#confirm-clear").click()
        assert page.locator("#review").is_hidden()
        assert page.locator("#destination").input_value() == ""
        assert page.locator("#budget-amount").input_value() == "270000"
        assert page.locator("#budget").input_value() == "270000"
        assert not page.locator("#consent").is_checked()
        assert page.locator("#children-ages input").count() == 0
        assert page.locator(".chip.active").count() == 0
        assert "Заявка не отправлена" in page.locator("#form-status").inner_text()
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        assert submissions == []

@pytest.mark.parametrize("width", [320, 390, 1280])
@pytest.mark.parametrize("asset_dir", [MINIAPP_DIR, MINIAPP_DIR.parent / "docs" / "miniapp"], ids=["source", "pages"])
def test_telegram_miniapp_browser_reviews_then_posts_v2_payload_and_closes(width, asset_dir):
    handler = partial(_QuietHandler, directory=str(asset_dir))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    captured = []

    with local_server(server):
        url = f"http://127.0.0.1:{server.server_port}/index.html"
        with sync_playwright() as pw, ExitStack() as resources:
            browser = pw.chromium.launch(channel="msedge", headless=True)
            resources.callback(browser.close)
            context = browser.new_context(viewport={"width": width, "height": 760})
            resources.callback(context.close)
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
            page.route("**/vendor/telegram-web-app.js*", lambda route: route.abort())

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
            page.locator('#trip-form[data-ready="true"]').wait_for()
            _assert_zoom_and_default_contrast(page)

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

            # Re-open review, then simulate closing/reopening before Save.
            # Review alone must not create a backend write or accidental lead.
            page.locator("#submit").click()
            page.locator("#review").wait_for(state="visible")
            assert captured == []
            page.reload(wait_until="domcontentloaded")
            page.locator('#trip-form[data-ready="true"]').wait_for()
            page.locator("#trip-form").wait_for(state="visible")
            assert page.locator("#review").is_hidden()
            assert captured == []
            assert "ничего не сохранится" in page.locator(".privacy-note").inner_text()

            # A reopened Mini App starts a fresh form. Fill it again and save
            # explicitly; only this action may create the one backend write.
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
            page.locator("#review").wait_for(state="visible")
            page.locator("#save").click()
            page.wait_for_function("window.__tgClosed === true")

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


def test_cancel_is_disabled_during_pending_save_and_recovers_after_error():
    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(_QuietHandler, directory=str(MINIAPP_DIR)))
    with local_server(server), sync_playwright() as pw, ExitStack() as resources:
        browser = pw.chromium.launch(channel="msedge", headless=True)
        resources.callback(browser.close)
        page = browser.new_page()
        page.add_init_script("""window.Telegram = {WebApp: {
            initData: 'synthetic-only', ready() {}, expand() {},
            BackButton: {show() {}, hide() {}, onClick(fn) { window.__back = fn; }}
        }};""")
        page.route("**/vendor/telegram-web-app.js*", lambda route: route.abort())
        pending = []
        page.route(API_URL, lambda route: pending.append(route))
        page.goto(f"http://127.0.0.1:{server.server_port}/index.html")
        page.locator('#trip-form[data-ready="true"]').wait_for()
        page.locator("#destination").fill("Сочи, Россия")
        page.locator("#consent").check()
        page.locator("#submit").click()
        with page.expect_request(API_URL):
            page.locator("#save").click()
        assert all(button.is_disabled() for button in page.locator('[data-clear-trip]').all())
        assert len(pending) == 1
        page.evaluate("window.__back()")
        assert page.locator("#review").is_visible()
        assert not page.locator("#clear-dialog").is_visible()
        pending[0].fulfill(status=500, content_type="application/json", body='{"ok":false}', headers={"Access-Control-Allow-Origin": "*"})
        playwright_sync.expect(page.locator('#review [data-clear-trip]')).to_be_enabled()
        page.get_by_role("button", name="Отменить подбор", exact=True).click()
        page.locator("#confirm-clear").click()
        assert page.locator("#trip-form").is_visible()
        assert page.locator("#destination").input_value() == ""
        assert len(pending) == 1


@pytest.mark.parametrize("failure", ["network", "http_500", "invalid_json"])
def test_failed_submit_preserves_review_and_retries_same_submission(failure):
    handler = partial(_QuietHandler, directory=str(MINIAPP_DIR))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    attempts = []
    with local_server(server), sync_playwright() as pw, ExitStack() as resources:
        browser = pw.chromium.launch(channel="msedge", headless=True)
        resources.callback(browser.close)
        context = browser.new_context(viewport={"width": 390, "height": 844})
        resources.callback(context.close)
        context.add_init_script("""
            window.__tgClosed = false;
            window.Telegram = {WebApp: {
                initData: 'synthetic-test-init-data',
                ready() {}, expand() {},
                close() { window.__tgClosed = true; }
            }};
        """)
        page = context.new_page()
        page.route("**/vendor/telegram-web-app.js*", lambda route: route.abort())
        def submit(route, request):
            attempts.append(request.post_data_json)
            if len(attempts) == 1 and failure == "network":
                route.abort()
                return
            first = len(attempts) == 1
            route.fulfill(
                status=500 if first and failure == "http_500" else 200,
                content_type="application/json",
                body="not json" if first and failure == "invalid_json" else ('{"ok":false}' if first else '{"ok":true}'),
                headers={"Access-Control-Allow-Origin": "*"},
            )
        page.route(API_URL, submit)
        page.goto(f"http://127.0.0.1:{server.server_port}/index.html")
        page.locator('#trip-form[data-ready="true"]').wait_for()
        page.locator("#destination").fill("Пхукет, Таиланд")
        page.locator("#departure").fill("Архангельск")
        page.locator("#consent").check()
        page.locator("#submit").click()
        page.locator("#review").wait_for(state="visible")
        summary = page.locator("#summary").inner_text()
        page.locator("#save").click()
        page.wait_for_function("document.getElementById('review').getAttribute('aria-busy') === 'false' && document.getElementById('status').textContent !== ''")
        assert len(attempts) == 1
        assert not page.evaluate("window.__tgClosed")
        assert page.locator("#review").is_visible()
        assert page.locator("#summary").inner_text() == summary
        assert page.locator("#save").is_enabled()
        assert page.locator("#edit").is_enabled()
        assert all(button.is_enabled() for button in page.locator('[data-clear-trip]').all())
        page.locator("#save").click()
        page.wait_for_function("window.__tgClosed === true")
        assert len(attempts) == 2
        assert attempts[0] == attempts[1]
        assert attempts[0]["submissionId"]


def test_telegram_flight_fallback_available_after_primary_browser_failure():
    import ast
    from flask import Flask, Response, request
    from urllib.parse import urlencode, urlsplit
    # Exercise the real renderer without booting the bot/AI stack in browser CI.
    source = MINIAPP_DIR.parent / 'bot.py'
    renderer = next(node for node in ast.parse(source.read_text(encoding='utf-8')).body
                    if isinstance(node, ast.FunctionDef) and node.name == 'telegram_miniapp_asset')
    renderer.decorator_list = []
    namespace = {'__file__': str(source), 'Response': Response, 'request': request}
    exec(compile(ast.Module(body=[renderer], type_ignores=[]), str(source), 'exec'), namespace)
    with Flask(__name__).test_request_context('/tg/miniapp/'):
        html = namespace['telegram_miniapp_asset']().get_data(as_text=True)
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel='msedge', headless=True)
        try:
            page = browser.new_page(viewport={'width': 390, 'height': 850})
            page.add_init_script("""window.__nativeEvents = [];
                window.TelegramWebviewProxy = {postEvent(type, data) {
                    window.__nativeEvents.push({type, data: JSON.parse(data || '{}')});
                }};""")
            origin = 'https://miniapp.example.test'
            submissions = []
            def route_request(route):
                url = urlsplit(route.request.url)
                if url.path == '/miniapp/submit':
                    submissions.append(route.request.url)
                    route.abort()
                elif route.request.url.startswith(origin + '/tg/miniapp/'):
                    name = url.path.removeprefix('/tg/miniapp/') or 'index.html'
                    if name == 'index.html':
                        route.fulfill(content_type='text/html', body=html)
                    elif name in ('app.js','styles.css','vendor/telegram-web-app.js','departure-support.js'):
                        route.fulfill(path=MINIAPP_DIR / name)
                    else:
                        route.abort()
                else:
                    route.abort()
            page.route('**/*', route_request)
            fragment = urlencode({'tgWebAppData': INIT_DATA, 'tgWebAppVersion':'8.0', 'tgWebAppPlatform':'tdesktop'})
            page.goto(origin + '/tg/miniapp/#' + fragment)
            page.locator('#flights').click()
            # The native browser does not report external connection failure back.
            # Leave its open event unanswered and verify fallback remains usable.
            page.locator('#flights-fallback').click()
            events = page.evaluate("window.__nativeEvents.filter(e => e.type === 'web_app_open_link')")
            assert len(events) == 2
            assert events[0]['data']['url'].startswith('https://travel.r0meo1.ru/')
            assert events[1]['data']['url'] == 'https://www.aviasales.com/?marker=778488'
            assert 'tgWebAppData' not in events[1]['data']['url']
            assert not submissions
            assert page.locator('#flights-fallback').is_enabled()
        finally:
            browser.close()
