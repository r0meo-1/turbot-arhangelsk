import base64
import hashlib
import hmac
from pathlib import Path
from contextlib import ExitStack
import time
from urllib.parse import urlencode

import pytest

from e2e_resources import local_server
from flask import Flask
from werkzeug.serving import make_server

playwright_sync = pytest.importorskip("playwright.sync_api", reason="VK Mini App browser E2E runs in Edge Bot CI")
sync_playwright = playwright_sync.sync_playwright

from shared.vk_miniapp import create_blueprint as _create_blueprint


def create_blueprint(save_draft, settings):
    def continuation(uid, request_id, info):
        save_draft(uid, info)
        return dict(ok=True, draftSaved=True, reviewSent=True, retryable=False), 200
    return _create_blueprint(save_draft, settings, continuation, lambda *args: True)


SECRET = "vk-miniapp-e2e-secret"
APP_ID = "54475121"
GROUP_ID = 240310110
USER_ID = 424242
REVIEW_COMMAND = "Проверить заявку"
REVIEW_PAYLOAD = {"command": "miniapp_review", "version": 1}


def _signed_launch_params(platform="desktop_web"):
    params = {
        "vk_app_id": APP_ID,
        "vk_group_id": str(GROUP_ID),
        "vk_platform": platform,
        "vk_ts": str(int(time.time())),
        "vk_user_id": str(USER_ID),
    }
    signed = urlencode(sorted(params.items()))
    params["sign"] = base64.urlsafe_b64encode(
        hmac.new(SECRET.encode(), signed.encode(), hashlib.sha256).digest()
    ).decode().rstrip("=")
    return urlencode(params)



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

def _fill_review_and_save(page, destination="Пхукет, Таиланд"):
    page.locator('#trip-form[data-ready="true"]').wait_for()
    if destination not in ("Сочи", "Казань", "Калининград", "Кисловодск", "Ессентуки", "Пятигорск", "Адлер"):
        page.locator('[data-destination-direction="abroad"]').click()
    page.locator("#destination").fill(destination)
    page.locator("#departure").fill("Архангельск")
    page.locator("#consent").check()
    page.locator("#terms-accepted").check()
    page.locator("#submit").click()
    page.locator("#review").wait_for(state="visible")
    page.locator("#save").click()
    _wait_for_chat(page)


def _wait_for_chat(page):
    try:
        page.locator('#chat').wait_for(state='visible', timeout=20000)
    except Exception as error:
        pytest.fail(f'Chat handoff failed: status={page.locator("#status").inner_text()!r}; save_disabled={page.locator("#save").is_disabled()}; {type(error).__name__}')


def _mock_bridge(page):
    # Test the app/Bridge protocol without the bundled SDK's host detection,
    # browser tracking protection or unrelated parent-window messaging.
    page.add_init_script('window.vkBridge={send(){return Promise.resolve({});},subscribe(){}};')
    page.on('requestfailed', lambda request: print('Browser transport failure:', request.failure, request.url.split('?')[0]))
    page.on('pageerror', lambda error: print('Browser script failure:', str(error)))
    page.route('**/vk-bridge.js*', lambda route: route.fulfill(
        content_type='application/javascript',
        body='/* Bridge transport is installed before document scripts. */',
    ))


def test_form_cannot_navigate_or_submit_before_app_handlers_load():
    app = Flask(__name__)
    saved = []
    app.register_blueprint(create_blueprint(lambda *args: saved.append(args), lambda: (SECRET, APP_ID, GROUP_ID)))
    server = make_server('127.0.0.1', 0, app)
    with local_server(server), sync_playwright() as pw, ExitStack() as resources:
        browser = pw.chromium.launch(channel='msedge', headless=True)
        resources.callback(browser.close)
        page = browser.new_page()
        _mock_bridge(page)
        scripts, navigations = [], []
        page.route('**/app.js*', lambda route: scripts.append(route))
        page.on('request', lambda request: navigations.append(request.url) if request.is_navigation_request() else None)
        url = f'http://127.0.0.1:{server.server_port}/vk/miniapp/'
        with page.expect_request('**/app.js*'):
            page.goto(url, wait_until='commit')
        pw_api = playwright_sync
        pw_api.expect(page.locator('#submit')).to_be_disabled()
        page.locator('#destination').fill('QA LOCAL ONLY')
        page.locator('#destination').press('Enter')
        assert navigations == [url]
        assert saved == []
        assert len(scripts) == 1
        scripts[0].fulfill(content_type='application/javascript', body=Path('vk-miniapp/app.js').read_text(encoding='utf-8'))
        page.locator('#trip-form[data-ready="true"]').wait_for()
        pw_api.expect(page.locator('#submit')).to_be_enabled()


@pytest.mark.parametrize('width,appearance', [(320, 'light'), (390, 'light'), (390, 'dark'), (1280, 'light')])
def test_search_preview_back_cancel_and_theme_never_submit(width, appearance, tmp_path):
    saved = []
    app = Flask(__name__)
    app.register_blueprint(create_blueprint(lambda *args: saved.append(args), lambda: (SECRET, APP_ID, GROUP_ID)))
    server = make_server('127.0.0.1', 0, app)
    with local_server(server), sync_playwright() as pw, ExitStack() as resources:
        browser = pw.chromium.launch(channel='msedge', headless=True)
        resources.callback(browser.close)
        page = browser.new_page(viewport={'width': width, 'height': 850})
        errors, submissions = [], []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.on('request', lambda request: submissions.append(request.url) if '/draft' in request.url or '/continue' in request.url else None)
        page.route('**/vk-bridge.js*', lambda route: route.fulfill(content_type='application/javascript', body='''
          window.vkBridge = {
            subscribe(callback) { window.deliverVKConfig = callback; },
            send() { return Promise.reject(new Error('unsigned preview')); }
          };
        '''))
        page.goto(f'http://127.0.0.1:{server.server_port}/vk/miniapp/')
        page.locator('#trip-form[data-ready="true"]').wait_for()
        page.wait_for_function("!!document.getElementById('destination-options')")
        page.evaluate('appearance => window.deliverVKConfig({detail:{type:"VKWebAppUpdateConfig",data:{appearance,insets:{top:24,bottom:16}}}})', appearance)
        assert page.locator('html').get_attribute('data-appearance') == appearance
        _assert_zoom_and_default_contrast(page)
        page.get_by_role('button', name='Сочи', exact=True).click()
        page.locator('#budget-amount').fill('270123')
        assert page.locator('#budget').input_value() == '270123'
        page.locator('#children').fill('1')
        page.locator('#children-ages input').fill('0')
        page.locator('#consent').check()
        page.locator('#terms-accepted').check()
        page.locator('#budget-amount').fill('900000')
        page.locator('#submit').click()
        assert page.locator('#review').is_hidden()
        page.locator('#budget-amount').fill('270123')
        page.locator('#submit').click()
        assert page.locator('#review').is_visible()
        assert page.locator('#save').is_disabled()
        assert '270 123' in page.locator('#summary').inner_text().replace('\u00a0', ' ')
        page.locator('#edit').click()
        assert page.locator('#destination').input_value() == 'Сочи'
        assert page.locator('#children-ages input').input_value() == '0'
        assert page.locator('#budget-amount').input_value() == '270123'
        assert page.locator('#step-parameters').get_attribute('aria-current') == 'step'
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        page.locator('#destination').scroll_into_view_if_needed()
        page.screenshot(path=str(tmp_path / f'vk-search-{width}-{appearance}.png'), full_page=True)
        page.locator('#submit').click()
        page.locator('#cancel-trip').click()
        page.locator('#keep-trip').click()
        assert page.locator('#review').is_visible()
        page.locator('#cancel-trip').click()
        page.locator('#confirm-clear').click()
        assert page.locator('#trip-form').is_visible()
        assert page.locator('#destination').input_value() == ''
        assert page.locator('#date-to').input_value() == ''
        assert page.locator('#date-to').get_attribute('min') == page.locator('#date').input_value()
        assert page.locator('#children-ages input').count() == 0
        assert page.locator('#consent').is_checked() is False
        assert page.locator('#terms-accepted').is_checked() is False
        assert 'не отправлена' in page.locator('#local-status').inner_text()
        assert saved == [] and submissions == [] and errors == []


@pytest.mark.parametrize("locale,width", [("en-US", 1280), ("en-US", 320), ("ru-RU", 390)])
def test_flight_date_uses_russian_display_and_iso_payload(locale, width, tmp_path):
    saved = []
    app = Flask(__name__)
    app.register_blueprint(create_blueprint(
        lambda uid, info: saved.append(info), lambda: (SECRET, APP_ID, GROUP_ID)
    ))
    server = make_server("127.0.0.1", 0, app)
    with local_server(server):
        with sync_playwright() as pw, ExitStack() as resources:
            browser = pw.chromium.launch(channel="msedge", headless=True)
            resources.callback(browser.close)
            page = browser.new_page(locale=locale, viewport={"width": width, "height": 900})
            _mock_bridge(page)
            page.goto(f"http://127.0.0.1:{server.server_port}/vk/miniapp/?{_signed_launch_params()}")
            page.locator('#trip-form[data-ready="true"]').wait_for()
            display = page.get_by_label("Дата вылета", exact=True)
            native = page.get_by_label("Выбрать дату в календаре", exact=True)
            assert display.input_value() == ".".join(reversed(native.input_value().split("-")))
            native.fill("2032-10-28")
            assert display.input_value() == "28.10.2032"
            display.fill("31.02.2032")
            assert not display.evaluate("el => el.checkValidity()")
            assert native.input_value() == ""
            display.fill("01.01.2000")
            assert not display.evaluate("el => el.checkValidity()")
            display.fill("")
            assert not display.evaluate("el => el.checkValidity()")
            display.fill("29.02.2032")
            assert display.evaluate("el => el.checkValidity()")
            assert native.input_value() == "2032-02-29"
            display.scroll_into_view_if_needed()
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            page.screenshot(path=str(tmp_path / f"flight-date-{locale}-{width}.png"))
            page.evaluate("() => { window.vkBridge.send = () => Promise.reject(new Error('test fallback')); }")
            _fill_review_and_save(page)
            assert "29.02.2032" in page.locator("#summary").inner_text()
            assert len(saved) == 1
            assert saved[0]["dates"] == "2032-02-29"


@pytest.mark.parametrize("width", [320, 390, 1280])
def test_vk_miniapp_browser_roundtrip_uses_server_review_without_bridge_payload(width):
    saved = []

    def save_draft(uid, info):
        saved.append((uid, info))

    app = Flask(__name__)
    app.register_blueprint(
        create_blueprint(save_draft, lambda: (SECRET, APP_ID, GROUP_ID))
    )

    server = make_server("127.0.0.1", 0, app)

    with local_server(server):
        url = f"http://127.0.0.1:{server.server_port}/vk/miniapp/?{_signed_launch_params()}"
        with sync_playwright() as pw, ExitStack() as resources:
            browser = pw.chromium.launch(channel="msedge", headless=True)
            resources.callback(browser.close)
            context = browser.new_context(viewport={"width": width, "height": 760})
            resources.callback(context.close)
            page = context.new_page()
            _mock_bridge(page)
            page.goto(url, wait_until="domcontentloaded")
            page.locator('#trip-form[data-ready="true"]').wait_for()
            _assert_zoom_and_default_contrast(page)

            overflow = page.evaluate(
                """() => [...document.querySelectorAll('body *')]
                  .map((el) => {
                    const rect = el.getBoundingClientRect();
                    return {
                      tag: el.tagName,
                      id: el.id || '',
                      cls: String(el.className || ''),
                      left: Math.round(rect.left * 10) / 10,
                      right: Math.round(rect.right * 10) / 10,
                      width: Math.round(rect.width * 10) / 10
                    };
                  })
                  .filter((item) => item.left < -0.5 || item.right > window.innerWidth + 0.5)
                  .slice(0, 12)"""
            )
            assert page.evaluate(
                "document.documentElement.scrollWidth <= window.innerWidth"
            ), overflow
            shell_box = page.locator(".shell").bounding_box()
            assert shell_box is not None
            assert shell_box["x"] >= 0
            assert shell_box["x"] + shell_box["width"] <= width + 0.5

            for label in (
                "Направление",
                "Город вылета по России",
                "Ночей",
                "Дата вылета",
                "Взрослых",
                "Детей до 18",
                "Бюджет на всю поездку",
            ):
                assert page.get_by_label(label, exact=True).count() == 1

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
            destination_options = page.locator("#destination-options option").evaluate_all(
                "els => els.map((el) => el.value)"
            )
            assert "Сочи" in destination_options
            assert "Казань" in destination_options
            assert "Пхукет, Таиланд" not in destination_options
            page.locator('[data-destination-direction="abroad"]').click()
            destination_options = page.locator("#destination-options option").evaluate_all(
                "els => els.map((el) => el.value)"
            )
            departure_options = page.locator("#departure-cities option").evaluate_all(
                "els => els.map((el) => el.value)"
            )
            assert "Пхукет, Таиланд" in destination_options
            assert "Хургада, Египет" in destination_options
            assert "Архангельск" in departure_options
            assert "Москва" in departure_options

            # Review is still local. Closing/reopening before Save must not
            # create a server-side draft or accidental lead.
            page.locator("#destination").fill("Пхукет, Таиланд")
            page.locator("#departure").fill("Архангельск")
            page.locator("#consent").check()
            page.locator("#terms-accepted").check()
            page.locator("#submit").click()
            page.locator("#review").wait_for(state="visible")
            assert saved == []
            assert "Заявка ещё не отправлена менеджеру." in page.locator("#review").inner_text()
            page.reload(wait_until="domcontentloaded")
            page.locator('#trip-form[data-ready="true"]').wait_for()
            page.locator("#trip-form").wait_for(state="visible")
            assert page.locator("#review").is_hidden()
            assert saved == []
            assert "заявка не сохранится" in page.locator(".privacy-note").inner_text()

            page.evaluate(
                """
                () => {
                  window.__vkBridgeCalls = [];
                  window.vkBridge.send = (method, params) => {
                    window.__vkBridgeCalls.push({ method, params });
                    return Promise.resolve({ result: true });
                  };
                }
                """
            )

            _fill_review_and_save(page)
            assert page.locator("#status").inner_text() == "Параметры отправлены в чат. Заявка менеджеру не отправлена."
            assert page.locator("#chat").get_attribute("href") == f"https://vk.ru/im?sel=-{GROUP_ID}"
            calls = page.evaluate("() => window.__vkBridgeCalls")
            assert not any(call['method'] in ('VKWebAppSendPayload', 'VKWebAppCopyText') for call in calls)

    assert len(saved) == 1
    uid, info = saved[0]
    assert uid == USER_ID
    assert info["destination"] == "Пхукет, Таиланд"
    assert info["origin"] == "Архангельск"
    assert info["source"] == "vk_mini_app"
    assert info["budget_scope"] == "total"

@pytest.mark.parametrize('bridge_result', ['pending', 'rejected', 'delayed'])
def test_vk_form_initializes_before_bridge_launch_params(bridge_result):
    """An unanswered bridge must not freeze the unsigned preview form."""
    from urllib.parse import parse_qsl

    saved = []
    app = Flask(__name__)
    app.register_blueprint(create_blueprint(
        lambda uid, info: saved.append((uid, info)),
        lambda: (SECRET, APP_ID, GROUP_ID),
    ))
    server = make_server('127.0.0.1', 0, app)
    with local_server(server):
        with sync_playwright() as pw, ExitStack() as resources:
            browser = pw.chromium.launch(channel='msedge', headless=True)
            resources.callback(browser.close)
            page = browser.new_page()
            page.route('**/vk-bridge.js*', lambda route: route.fulfill(
                content_type='application/javascript',
                body='''window.vkBridge = {send(method) {
                  if (method === 'VKWebAppGetLaunchParams') {
                    return new Promise((resolve, reject) => {
                      window.resolveLaunch = resolve;
                      window.rejectLaunch = reject;
                    });
                  }
                  return Promise.resolve({});
                }};''',
            ))
            page.goto(f'http://127.0.0.1:{server.server_port}/vk/miniapp/')
            page.locator('#trip-form[data-ready="true"]').wait_for()
            page.wait_for_function("document.getElementById('date').value !== ''", timeout=2000)
            page.locator('[data-destination-direction="abroad"]').click()
            page.get_by_role('button', name='Таиланд', exact=True).click()
            assert page.locator('#destination').input_value() == 'Таиланд'
            page.locator('#children').fill('2')
            assert page.locator('#children-ages input').count() == 2
            page.locator('#children-ages input').nth(0).fill('4')
            page.locator('#children-ages input').nth(1).fill('9')
            page.locator('#consent').check()
            page.locator('#terms-accepted').check()
            page.locator('#submit').click()
            assert page.locator('#review').is_visible()
            assert '4 лет, 9 лет' in page.locator('#summary').inner_text()
            assert page.locator('#save').is_disabled()
            assert not saved
            if bridge_result == 'rejected':
                page.evaluate("window.rejectLaunch(new Error('not available'))")
                assert page.locator('#save').is_disabled()
            elif bridge_result == 'delayed':
                page.evaluate('(params) => window.resolveLaunch(params)', dict(parse_qsl(_signed_launch_params())))
                page.wait_for_function("!document.getElementById('save').disabled")
                page.locator('#save').click()
                _wait_for_chat(page)
                assert len(saved) == 1


@pytest.mark.parametrize('platform,close_result', [
    ('desktop_app_messenger', 'success'), ('desktop_app_messenger', 'rejected'),
    ('desktop_app_messenger', 'pending'), ('mobile_android', 'success'), ('mobile_iphone', 'success')])
def test_server_delivery_and_native_return_never_need_a_manual_command(platform, close_result):
    saved = []
    app = Flask(__name__)
    app.register_blueprint(create_blueprint(lambda uid, info: saved.append(info), lambda: (SECRET, APP_ID, GROUP_ID)))
    server = make_server('127.0.0.1', 0, app)
    with local_server(server), sync_playwright() as pw, ExitStack() as resources:
        browser = pw.chromium.launch(channel='msedge', headless=True)
        resources.callback(browser.close)
        page = browser.new_page(viewport={'width': 390, 'height': 844})
        _mock_bridge(page)
        page.goto(f'http://127.0.0.1:{server.server_port}/vk/miniapp/?{_signed_launch_params(platform)}#bot')
        page.locator('#trip-form[data-ready="true"]').wait_for()
        page.evaluate('''result => {
          window.calls = [];
          window.vkBridge.send = (method, params) => {
            window.calls.push({method,params});
            if (method !== 'VKWebAppClose') return Promise.resolve({});
            if (result === 'pending') return new Promise(() => {});
            if (result === 'rejected') return Promise.reject(new Error('unsupported'));
            return Promise.resolve({result:true});
          };
        }''', close_result)
        page.locator('#destination').fill('Сочи')
        page.locator('#budget-amount').fill('270123')
        from datetime import date, timedelta
        first_date = page.locator('#date').input_value()
        last_date = (date.fromisoformat(first_date) + timedelta(days=7)).isoformat()
        page.locator('#nights-mode').select_option('range')
        page.locator('#nights').fill('7')
        page.locator('#nights-to').fill('10')
        page.locator('#date-mode').select_option('range')
        page.locator('#date-to').fill(last_date)
        page.locator('#consent').check()
        page.locator('#terms-accepted').check()
        page.locator('#submit').click()
        page.locator('#save').click()
        playwright_sync.expect(page.locator('#status')).to_have_text('Параметры отправлены в чат. Заявка менеджеру не отправлена.')
        assert len(saved) == 1 and saved[0]['budget'] == 270123
        assert saved[0]['dates'] == f'{first_date} — {last_date}' and saved[0]['nights'] == '7-10'
        if platform == 'desktop_app_messenger':
            page.locator('#return-chat').click()
            if close_result != 'success':
                playwright_sync.expect(page.locator('#status')).to_contain_text('крестиком', timeout=5000)
            assert page.locator('#chat').is_hidden()
            assert page.evaluate("window.calls.filter(c => c.method === 'VKWebAppClose')") == [
                {'method': 'VKWebAppClose', 'params': {'status': 'success'}}]
        else:
            assert page.locator('#chat').is_visible()
            assert page.locator('#return-chat').is_hidden()
        assert not page.evaluate("window.calls.some(c => ['VKWebAppSendPayload','VKWebAppCopyText'].includes(c.method))")


@pytest.mark.parametrize('denied', [True, False])
def test_saved_but_undelivered_draft_shows_retry_and_keeps_operation_identity(denied):
    saved, attempts = [], []
    app = Flask(__name__)
    app.register_blueprint(create_blueprint(lambda *a: saved.append(a), lambda: (SECRET, APP_ID, GROUP_ID)))
    server = make_server('127.0.0.1', 0, app)
    with local_server(server), sync_playwright() as pw, ExitStack() as resources:
        browser = pw.chromium.launch(channel='msedge', headless=True)
        resources.callback(browser.close)
        page = browser.new_page()
        _mock_bridge(page)
        def reply(route, request):
            attempts.append(request.post_data_json)
            if len(attempts) == 1:
                import json
                route.fulfill(content_type='application/json', body=json.dumps(dict(
                    ok=True, draftSaved=True, reviewSent=False, retryable=not denied, groupId=GROUP_ID,
                    error='Разрешите сообщения сообщества.' if denied else 'Доставка не подтверждена.')))
            else:
                route.continue_()
        page.route('**/vk/miniapp/continue', reply)
        page.goto(f'http://127.0.0.1:{server.server_port}/vk/miniapp/?{_signed_launch_params()}')
        page.locator('#trip-form[data-ready="true"]').wait_for()
        page.locator('#destination').fill('Сочи')
        page.locator('#consent').check()
        page.locator('#terms-accepted').check()
        page.locator('#submit').click()
        page.locator('#save').click()
        playwright_sync.expect(page.locator('#save')).to_have_text('Повторить переход')
        assert page.locator('#chat').is_hidden()
        assert 'отправлены в чат' not in page.locator('#status').inner_text()
        page.locator('#save').click()
        _wait_for_chat(page)
        assert attempts[0]['requestId'] == attempts[1]['requestId']
        assert len(saved) == 1


@pytest.mark.parametrize("failure", ["network", "http_500", "http_429", "invalid_json"])
def test_failed_draft_keeps_review_and_allows_explicit_retry(failure):
    saved = []
    attempts = []
    app = Flask(__name__)
    app.register_blueprint(create_blueprint(
        lambda uid, info: saved.append((uid, info)),
        lambda: (SECRET, APP_ID, GROUP_ID),
    ))
    server = make_server("127.0.0.1", 0, app)
    with local_server(server), sync_playwright() as pw, ExitStack() as resources:
        browser = pw.chromium.launch(channel="msedge", headless=True)
        resources.callback(browser.close)
        context = browser.new_context(viewport={"width": 390, "height": 844})
        resources.callback(context.close)
        page = context.new_page()
        def submit(route, request):
            attempts.append(request.post_data_json)
            if len(attempts) > 1:
                route.continue_()
            elif failure == "network":
                route.abort()
            else:
                route.fulfill(
                    status=429 if failure == "http_429" else (500 if failure == "http_500" else 200),
                    content_type="application/json",
                    body='{"ok":false,"error":"Подождите минуту и повторите переход."}' if failure == "http_429" else ('{"ok":false}' if failure == "http_500" else "not json"),
                )
        page.route("**/vk/miniapp/continue", submit)
        page.goto(f"http://127.0.0.1:{server.server_port}/vk/miniapp/?{_signed_launch_params()}")
        page.locator('#trip-form[data-ready="true"]').wait_for()
        page.locator('[data-destination-direction="abroad"]').click()
        page.locator("#destination").fill("Пхукет, Таиланд")
        page.locator("#departure").fill("Архангельск")
        page.locator("#consent").check()
        page.locator("#terms-accepted").check()
        page.locator("#submit").click()
        page.locator("#review").wait_for(state="visible")
        summary = page.locator("#summary").inner_text()
        page.locator("#save").click()
        page.wait_for_function("document.getElementById('review').getAttribute('aria-busy') === 'false' && document.getElementById('status').textContent !== ''")
        assert len(attempts) == 1
        assert saved == []
        assert page.locator("#chat").is_hidden()
        assert page.locator("#review").is_visible()
        assert page.locator("#summary").inner_text() == summary
        assert page.locator("#edit").is_enabled()
        assert page.locator("#save").is_enabled()
        page.locator("#save").click()
        _wait_for_chat(page)
        assert len(attempts) == 2
        assert attempts[0] == attempts[1]
        assert len(saved) == 1


@pytest.mark.parametrize('expired', [False, True])
def test_clear_after_reload_cancels_the_durable_pending_operation(expired):
    from uuid import uuid4
    rid, cancelled = str(uuid4()), []
    app = Flask(__name__)
    app.register_blueprint(_create_blueprint(lambda *a: None, lambda: (SECRET, APP_ID, GROUP_ID),
        cancel_continue=lambda uid, request_id: cancelled.append((uid, request_id)) or True))
    server = make_server('127.0.0.1', 0, app)
    with local_server(server), sync_playwright() as pw, ExitStack() as resources:
        browser = pw.chromium.launch(channel='msedge', headless=True)
        resources.callback(browser.close)
        page = browser.new_page()
        _mock_bridge(page)
        page.goto(f'http://127.0.0.1:{server.server_port}/vk/miniapp/?{_signed_launch_params()}')
        page.locator('#trip-form[data-ready="true"]').wait_for()
        page.evaluate('rid => { const p=new URLSearchParams(location.search); sessionStorage.setItem(`vk-miniapp-continue:${p.get("vk_app_id")}:${p.get("vk_user_id")}`, JSON.stringify({id:rid,hash:"pending",created:0})); }', rid)
        page.reload()
        page.locator('#trip-form[data-ready="true"]').wait_for()
        if expired:
            page.route('**/vk/miniapp/continue/cancel', lambda route: route.fulfill(
                status=401, content_type='application/json', body='{"ok":false,"error":"Откройте приложение заново из VK."}'))
        page.locator('#clear-trip').click()
        page.locator('#confirm-clear').click()
        if expired:
            playwright_sync.expect(page.locator('#status')).to_contain_text('Откройте приложение заново из VK.')
            assert not cancelled and page.evaluate('sessionStorage.length') == 1
            return
        playwright_sync.expect(page.locator('#local-status')).to_contain_text('Параметры очищены')
        assert len(cancelled) == 1 and cancelled[0][1] == rid
        assert page.evaluate('sessionStorage.length') == 0

@pytest.mark.parametrize('failure', ['offline','invalid_json','http_500'])
def test_legal_documents_keep_verified_identity_when_config_fails(failure):
    from shared.legal_identity import LEGAL_OPERATOR_DISPLAY, LEGAL_PRIVACY_CONTACT
    app = Flask(__name__)
    app.register_blueprint(create_blueprint(lambda *_: pytest.fail('no writes'), lambda: (SECRET,APP_ID,GROUP_ID)))
    server = make_server('127.0.0.1', 0, app)
    with local_server(server), sync_playwright() as pw, ExitStack() as resources:
        browser=pw.chromium.launch(channel='msedge',headless=True)
        resources.callback(browser.close)
        page=browser.new_page(viewport={'width':390,'height':850})
        def fail_config(route):
            if failure=='offline':
                return route.abort()
            route.fulfill(status=500 if failure=='http_500' else 200,
                          content_type='application/json',body='invalid')
        page.route('**/legal.json', fail_config)
        for name in ('privacy.html','consent.html'):
            page.goto(f'http://127.0.0.1:{server.server_port}/vk/miniapp/{name}')
            playwright_sync.expect(page.locator('[data-legal-operator]')).to_have_text(LEGAL_OPERATOR_DISPLAY)
            playwright_sync.expect(page.locator('[data-legal-contact]')).to_have_text(LEGAL_PRIVACY_CONTACT)
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
