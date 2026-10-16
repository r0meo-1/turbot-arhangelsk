from pathlib import Path
from urllib.parse import urlsplit

import pytest

pw = pytest.importorskip('playwright.sync_api')


@pytest.mark.parametrize('surface,sdk', [('miniapp', 'vendor/telegram-web-app.js'), ('vk-miniapp', 'vk-bridge.js')])
def test_form_paints_while_sdk_is_still_loading(surface, sdk):
    root = Path(__file__).resolve().parents[1] / surface
    with pw.sync_playwright() as p:
        browser = p.chromium.launch(channel='msedge', headless=True)
        try:
            page = browser.new_page(viewport={'width': 390, 'height': 844})
            pending = []
            def asset(route):
                name = urlsplit(route.request.url).path.lstrip('/') or 'index.html'
                if name == 'miniapp/departures':
                    route.fulfill(json={'complete': False, 'departures': []})
                    return
                if name == sdk:
                    pending.append(route)
                else:
                    route.fulfill(path=root / name)
            page.route('https://miniapp.example.test/**', asset)
            page.goto('https://miniapp.example.test/', wait_until='commit')
            pw.expect(page.locator('h1')).to_be_visible()
            pw.expect(page.locator('#destination')).to_be_visible()
            pw.expect(page.locator('#submit')).to_be_disabled()
            assert pending
            pending[0].fulfill(path=root / sdk)
            page.locator('#trip-form[data-ready="true"]').wait_for()
            pw.expect(page.locator('#submit')).to_be_enabled()
        finally:
            browser.close()


@pytest.mark.parametrize('surface', ['miniapp', 'vk-miniapp'])
@pytest.mark.parametrize('complete', [True, False, None])
def test_departure_advice_never_changes_city_without_explicit_choice(surface, complete):
    root = Path(__file__).resolve().parents[1] / surface
    with pw.sync_playwright() as p:
        browser = p.chromium.launch(channel='msedge', headless=True)
        try:
            page = browser.new_page(viewport={'width': 390, 'height': 844})
            requests = []
            page.on('request', lambda request: requests.append(request.method))
            def asset(route):
                name = urlsplit(route.request.url).path.lstrip('/') or 'index.html'
                if name == 'miniapp/departures':
                    if complete is None:
                        route.abort()
                    else:
                        route.fulfill(json={'complete': complete, 'departures': ['Москва']})
                else:
                    route.fulfill(path=root / name)
            page.route('https://miniapp.example.test/**', asset)
            page.goto('https://miniapp.example.test/')
            page.locator('#trip-form[data-ready="true"]').wait_for()
            page.locator('#budget-amount').fill('270123')
            page.locator('#departure').fill('Архангельск')
            pw.expect(page.locator('#departure-support')).to_be_visible()
            assert page.locator('#departure').input_value() == 'Архангельск'
            assert page.get_by_role('button', name='Вылет из: Санкт-Петербург').count() == 0
            if complete:
                assert 'наличие не проверено' in page.locator('#departure-support').inner_text()
                page.get_by_role('button', name='Вылет из: Москва').click()
                assert page.locator('#departure').input_value() == 'Москва'
            else:
                assert page.get_by_role('button', name='Вылет из: Москва').count() == 0
                assert 'проверим при поиске' in page.locator('#departure-support').inner_text()
            assert page.locator('#budget-amount').input_value() == '270123'
            assert 'POST' not in requests
        finally:
            browser.close()
