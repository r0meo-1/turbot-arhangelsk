"""Real local backend + browser. No Telegram account or supplier traffic."""
from contextlib import contextmanager
from datetime import date, timedelta
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import time

import pytest
import requests

pw_api = pytest.importorskip("playwright.sync_api")
ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def demo_server(tmp_path_factory):
    directory = tmp_path_factory.mktemp("alean-browser")
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    with (directory / "server.log").open("w") as log:
        process = subprocess.Popen([sys.executable, str(ROOT / "tools/run_alean_demo.py"), "--port", str(port)],
                                   cwd=ROOT, stdout=log, stderr=log)
        url = f"http://127.0.0.1:{port}"
        try:
            for _ in range(150):
                if process.poll() is not None:
                    pytest.fail("Local demo server exited; inspect server.log")
                try:
                    with requests.get(url + "/miniapp/", timeout=1) as response:
                        if response.status_code == 200:
                            break
                except requests.RequestException:
                    pass
                time.sleep(.05)
            else:
                pytest.fail("Local demo server did not start")
            yield url
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill(); process.wait(timeout=5)


@contextmanager
def browser_page(width=390):
    with pw_api.sync_playwright() as pw:
        path = os.getenv("ALEAN_BROWSER_PATH") or shutil.which("chromium")
        options = {"executable_path": path} if path else {"channel": "msedge"}
        with pw.chromium.launch(headless=True, **options) as browser:
            with browser.new_context(viewport={"width": width, "height": 850}) as context:
                # Only localhost may be contacted. Even the Telegram SDK is blocked.
                context.route("https://**/*", lambda route: route.abort())
                context.add_init_script("""window.Telegram={WebApp:{initData:'',
                  ready(){},expand(){},HapticFeedback:{selectionChanged(){},notificationOccurred(){}},
                  BackButton:{show(){window.__tgBackVisible=true},hide(){window.__tgBackVisible=false},
                    onClick(fn){window.__tgBack=fn}}}};""")
                page = context.new_page()
                yield page


def capture(page, filename, *, chat=False):
    folder = os.getenv("ALEAN_SCREENSHOTS_DIR")
    if folder:
        path = Path(folder); path.mkdir(parents=True, exist_ok=True)
        options = {"style": "#messages .message:nth-last-child(n+5){display:none}"} if chat else {}
        page.screenshot(path=str(path / filename), full_page=True, **options)


def review(page):
    page.get_by_role("button", name="Проверить параметры", exact=True).click()
    pw_api.expect(page.locator("#alean-review")).to_be_visible()


def test_miniapp_entry_search_navigation_and_disabled_booking(demo_server):
    with browser_page() as page:
        page.goto(demo_server + "/miniapp/")
        capture(page, "miniapp-entry.png")
        page.get_by_role("link", name="Открыть поиск Алеан").click()
        pw_api.expect(page.get_by_role("heading", name="Отели и туры по России")).to_be_visible()
        page.get_by_label("Возраст детей", exact=True).fill("7, 12")
        capture(page, "miniapp-search.png")
        review(page)
        pending = []
        page.route("**/miniapp/alean/search", lambda route: pending.append(route))
        page.get_by_role("button", name="Найти варианты").click()
        pw_api.expect(page.locator("#search-status")).to_have_text("Ищем варианты в Алеан…")
        capture(page, "miniapp-loading.png")
        assert pending
        pending.pop().continue_()
        pw_api.expect(page.locator(".offer-card")).to_have_count(3)
        pw_api.expect(page.locator("#result-title")).to_have_text("Демо-результаты")
        assert page.get_by_role("button", name="Бронирование недоступно").first.is_disabled()
        capture(page, "miniapp-results.png")
        page.unroute("**/miniapp/alean/search")
        page.get_by_role("button", name="← Назад", exact=True).click()
        assert page.locator("#ages").input_value() == "7, 12"
        page.get_by_label("Направление", exact=True).select_option("Адлер")
        page.get_by_label("Отель", exact=True).check()
        page.get_by_label("Бюджет на всех, ₽", exact=False).fill("1")
        review(page); page.get_by_role("button", name="Найти варианты").click()
        pw_api.expect(page.locator("#search-status")).to_contain_text("не найдено")
        capture(page, "miniapp-empty.png")
        page.get_by_role("button", name="Отмена", exact=True).click()
        pw_api.expect(page.locator("#cancel-status")).to_contain_text("Ничего не отправлено")
        assert page.locator("#destination").input_value() == "Адлер"


@pytest.mark.parametrize("scenario,expected", [("timeout", "истекло"), ("error", "недоступен")])
def test_miniapp_provider_errors_and_retry(demo_server, scenario, expected):
    with browser_page() as page:
        page.goto(demo_server + "/miniapp/alean/alean.html")
        page.get_by_label("Демо-сценарий").select_option(scenario)
        review(page); page.get_by_role("button", name="Найти варианты").click()
        pw_api.expect(page.locator("#search-status")).to_contain_text(expected)
        pw_api.expect(page.get_by_role("button", name="Повторить поиск")).to_be_visible()
        capture(page, f"miniapp-{scenario}.png")
        page.get_by_role("button", name="← Назад", exact=True).click()
        page.get_by_label("Демо-сценарий").select_option("success")
        review(page); page.get_by_role("button", name="Найти варианты").click()
        pw_api.expect(page.locator(".offer-card")).to_have_count(3)


def test_miniapp_validation_and_cancel_pending_search(demo_server):
    with browser_page(320) as page:
        page.goto(demo_server + "/miniapp/alean/alean.html")
        page.locator("#departure").fill(page.locator("#arrival").input_value())
        page.get_by_role("button", name="Проверить параметры", exact=True).click()
        pw_api.expect(page.locator("#validation")).to_contain_text("1–28")
        capture(page, "miniapp-validation.png")
        start = date.today() + timedelta(days=30)
        page.locator("#departure").fill((start+timedelta(days=7)).isoformat())
        page.locator("#ages").fill("18")
        page.get_by_role("button", name="Проверить параметры", exact=True).click()
        pw_api.expect(page.locator("#validation")).to_contain_text("0–17")
        page.locator("#ages").fill("7,")
        page.get_by_role("button", name="Проверить параметры", exact=True).click()
        pw_api.expect(page.locator("#validation")).to_contain_text("0–17")
        page.locator("#ages").fill("7")
        review(page)
        pending = []
        page.route("**/miniapp/alean/search", lambda route: pending.append(route))
        page.get_by_role("button", name="Найти варианты").click()
        pw_api.expect(page.locator("#search-status")).to_contain_text("Ищем")
        page.get_by_role("button", name="Отмена", exact=True).click()
        pw_api.expect(page.locator("#alean-form")).to_be_visible()
        pw_api.expect(page.locator("#alean-results")).to_be_hidden()
        assert page.locator("#ages").input_value() == "7"
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")


def test_miniapp_browser_timeout(demo_server):
    with browser_page() as page:
        page.goto(demo_server + "/miniapp/alean/alean.html")
        page.clock.install()
        page.route("**/miniapp/alean/search", lambda route: None)
        review(page); page.get_by_role("button", name="Найти варианты").click()
        page.clock.fast_forward(46000)
        pw_api.expect(page.locator("#search-status")).to_contain_text("Время ожидания истекло")


def test_miniapp_telegram_host_back_keeps_form_answers(demo_server):
    with browser_page() as page:
        page.goto(demo_server + "/miniapp/alean/alean.html")
        page.locator("#ages").fill("7, 12")
        review(page)
        assert page.evaluate("window.__tgBackVisible") is True
        page.evaluate("window.__tgBack()")
        pw_api.expect(page.locator("#alean-form")).to_be_visible()
        assert page.locator("#ages").input_value() == "7, 12"
        assert page.evaluate("window.__tgBackVisible") is False


def test_telegram_real_handler_preview_search_back_cancel_failures(demo_server):
    with browser_page(640) as page:
        page.goto(demo_server + "/demo/telegram")
        page.locator("#start").click()
        def last_button(text):
            page.locator(".message.bot").last.get_by_role("button", name=text, exact=True).click()
        def send(text):
            page.get_by_label("Сообщение").fill(text)
            page.get_by_role("button", name="Отправить", exact=True).click()
            pw_api.expect(page.locator(".message.user").last).to_have_text(text)
        pw_api.expect(page.locator(".message.bot").last).to_contain_text("Что ищем")
        last_button("Тур с перелётом"); last_button("Сочи")
        start = date.today() + timedelta(days=30)
        for text in (start.isoformat(), (start+timedelta(days=7)).isoformat(), "2", "7", "150000", "Москва"):
            send(text)
        pw_api.expect(page.locator(".message.bot").last).to_contain_text("проверьте параметры")
        last_button("← Назад")
        pw_api.expect(page.locator(".message.bot").last).to_contain_text("Москва")
        last_button("Оставить ответ"); last_button("Искать")
        pw_api.expect(page.locator(".message.bot").last).to_contain_text("вымышленный")
        capture(page, "telegram-local-results.png", chat=True)
        for scenario, expected in (("empty", "не найдено"), ("timeout", "истекло"), ("error", "недоступен")):
            page.locator("#scenario").select_option(scenario)
            last_button("Повторить поиск")
            pw_api.expect(page.locator(".message.bot").last).to_contain_text(expected)
        capture(page, "telegram-local-error.png", chat=True)
        last_button("Изменить параметры")
        pw_api.expect(page.locator(".message.bot").last).to_contain_text("Сохранённый ответ")
        last_button("Отель"); last_button("Адлер")
        for text in (start.isoformat(), (start+timedelta(days=7)).isoformat(), "2", "нет", "150000"):
            send(text)
        page.locator("#scenario").select_option("success"); last_button("Искать")
        pw_api.expect(page.locator(".message.bot").last).to_contain_text("Адлер")
        last_button("Отмена")
        pw_api.expect(page.locator(".message.bot").last).to_contain_text("Ничего не отправлено")
        pw_api.expect(page.locator("#safety")).to_have_text("Реальных лидов: 0")


@pytest.mark.parametrize("scenario", ["success", "empty", "error", "timeout"])
def test_serverless_miniapp_sdk_transport_and_navigation(demo_server, scenario):
    """Official SDK call contract is stubbed; this is not Telegram authentication."""
    with browser_page() as page:
        def static(route):
            filename = route.request.url.split("?")[0].rsplit("/", 1)[-1] or "alean.html"
            assert filename in {"alean.html", "alean.js", "alean.css", "styles.css"}
            route.fulfill(path=str(ROOT / "miniapp" / filename))
        page.route("https://app8886586430.tgcloud.ai/**", static)
        page.add_init_script("""window.__sdkCalls=[];
          window.Telegram.WebApp.initData='FICTIONAL_SDK_TEST_NOT_SIGNED';
          window.Telegram.WebApp.Serverless={
          call(name,input,callback){window.__sdkCalls.push({name,input});window.__sdkCallback=callback}
        };""")
        page.goto("https://app8886586430.tgcloud.ai/")
        assert page.locator('#mode option[value="live"]').is_disabled()
        page.locator("#ages").fill("7")
        page.get_by_label("Демо-сценарий").select_option(scenario)
        review(page); page.get_by_role("button", name="Найти варианты").click()
        pw_api.expect(page.locator("#search-status")).to_contain_text("Ищем")
        call = page.evaluate("window.__sdkCalls.at(-1)")
        assert call["name"] == "aleanSearch"
        assert call["input"]["mode"] == "demo"
        assert set(call["input"]) == {"query", "mode", "scenario"}
        with requests.post(demo_server + "/miniapp/alean/search", json=call["input"], timeout=5) as response:
            result = response.json()
        page.evaluate("result=>window.__sdkCallback(null,result)", result)
        if scenario == "success":
            pw_api.expect(page.locator(".offer-card")).to_have_count(3)
            assert page.get_by_role("button", name="Бронирование недоступно").first.is_disabled()
            capture(page, "serverless-sdk-results.png")
        elif scenario == "empty":
            pw_api.expect(page.locator("#search-status")).to_contain_text("не найдено")
        else:
            pw_api.expect(page.get_by_role("button", name="Повторить поиск")).to_be_visible()
            page.get_by_role("button", name="Повторить поиск").click()
            assert len(page.evaluate("window.__sdkCalls")) == 2
        page.get_by_role("button", name="← Назад", exact=True).click()
        assert page.locator("#ages").input_value() == "7"
        review(page); page.get_by_role("button", name="Найти варианты").click()
        page.get_by_role("button", name="Отмена", exact=True).click()
        page.evaluate("result=>window.__sdkCallback(null,result)", result)
        pw_api.expect(page.locator("#alean-results")).to_be_hidden()
        pw_api.expect(page.locator("#cancel-status")).to_contain_text("Ничего не отправлено")


def test_serverless_miniapp_requires_telegram_launch_without_sdk_exception():
    with browser_page() as page:
        def static(route):
            filename = route.request.url.rsplit("/", 1)[-1] or "alean.html"
            route.fulfill(path=str(ROOT / "miniapp" / filename))
        page.route("https://app8886586430.tgcloud.ai/**", static)
        page.add_init_script("""window.Telegram.WebApp.Serverless={call(){
          throw new Error('SDK must not be called without Telegram initData');
        }};""")
        page.goto("https://app8886586430.tgcloud.ai/")
        review(page); page.get_by_role("button", name="Найти варианты").click()
        pw_api.expect(page.locator("#search-status")).to_have_text(
            "Откройте демо Mini App из Telegram для авторизации.")
