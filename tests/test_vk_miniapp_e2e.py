import base64
import hashlib
import hmac
from contextlib import ExitStack
import time
from urllib.parse import urlencode

import pytest

from e2e_resources import local_server
from flask import Flask
from werkzeug.serving import make_server

playwright_sync = pytest.importorskip("playwright.sync_api", reason="VK Mini App browser E2E runs in Edge Bot CI")
sync_playwright = playwright_sync.sync_playwright

from shared.vk_miniapp import create_blueprint


SECRET = "vk-miniapp-e2e-secret"
APP_ID = "54475121"
GROUP_ID = 240310110
USER_ID = 424242
REVIEW_COMMAND = "Проверить заявку"
REVIEW_PAYLOAD = {"command": "miniapp_review", "version": 1}


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
    page.locator("#destination").fill(destination)
    page.locator("#departure").fill("Архангельск")
    page.locator("#consent").check()
    page.locator("#terms-accepted").check()
    page.locator("#submit").click()
    page.locator("#review").wait_for(state="visible")
    page.locator("#save").click()
    page.locator("#chat").wait_for(state="visible")


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
            page.goto(f"http://127.0.0.1:{server.server_port}/vk/miniapp/?{_signed_launch_params()}")
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
def test_vk_miniapp_browser_roundtrip_sends_review_payload_with_clipboard_fallback(width):
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
            page.goto(url, wait_until="domcontentloaded")
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
                "Вылет",
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
            page.wait_for_function(
                "() => document.querySelector('#status').textContent.includes('вводить команду не нужно')"
            )

            assert page.locator("#status").inner_text().startswith("Параметры сохранены.")
            assert page.locator("#chat").get_attribute("href") == f"https://vk.ru/im?sel=-{GROUP_ID}"
            send_payload_calls = page.evaluate(
                "() => window.__vkBridgeCalls.filter((call) => call.method === 'VKWebAppSendPayload')"
            )
            assert send_payload_calls == [{
                "method": "VKWebAppSendPayload",
                "params": {"group_id": GROUP_ID, "payload": REVIEW_PAYLOAD},
            }]
            copy_calls = page.evaluate(
                "() => window.__vkBridgeCalls.filter((call) => call.method === 'VKWebAppCopyText')"
            )
            assert copy_calls == []

            # app_payload is the preferred automatic handoff. If that Bridge
            # command is unavailable on a client, the already-saved draft must
            # stay successful and fall back to copying the review command.
            fallback_page = context.new_page()
            fallback_page.goto(url, wait_until="domcontentloaded")
            fallback_page.evaluate(
                """
                () => {
                  window.__vkBridgeCalls = [];
                  window.vkBridge.send = (method, params) => {
                    window.__vkBridgeCalls.push({ method, params });
                    if (method === 'VKWebAppSendPayload') {
                      return Promise.reject(new Error('payload unavailable'));
                    }
                    return Promise.resolve({ result: true });
                  };
                }
                """
            )
            _fill_review_and_save(fallback_page)
            fallback_page.wait_for_function(
                "() => document.querySelector('#status').textContent.includes('скопирована')"
            )

            fallback_status = fallback_page.locator("#status").inner_text()
            assert REVIEW_COMMAND in fallback_status
            assert "скопирована" in fallback_status
            assert fallback_page.locator("#chat").get_attribute("href") == f"https://vk.ru/im?sel=-{GROUP_ID}"
            fallback_calls = fallback_page.evaluate("() => window.__vkBridgeCalls")
            assert [call["method"] for call in fallback_calls] == [
                "VKWebAppSendPayload",
                "VKWebAppCopyText",
            ]
            assert fallback_calls[0]["params"] == {
                "group_id": GROUP_ID,
                "payload": REVIEW_PAYLOAD,
            }
            assert fallback_calls[1]["params"] == {"text": REVIEW_COMMAND}

    assert len(saved) == 2
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
            page.route('**/vk-bridge.js', lambda route: route.fulfill(
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
            page.wait_for_function("document.getElementById('date').value !== ''", timeout=2000)
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
                page.locator('#chat').wait_for(state='visible')
                assert len(saved) == 1


@pytest.mark.parametrize("failure", ["network", "http_500", "invalid_json"])
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
                    status=500 if failure == "http_500" else 200,
                    content_type="application/json",
                    body='{"ok":false}' if failure == "http_500" else "not json",
                )
        page.route("**/vk/miniapp/draft", submit)
        page.goto(f"http://127.0.0.1:{server.server_port}/vk/miniapp/?{_signed_launch_params()}")
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
        page.locator("#chat").wait_for(state="visible")
        assert len(attempts) == 2
        assert attempts[0] == attempts[1]
        assert len(saved) == 1
