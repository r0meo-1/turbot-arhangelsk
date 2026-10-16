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
def test_flexible_date_and_nights_modes_validate_and_keep_values_on_back(surface):
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
                    route.fulfill(json={'complete': True, 'departures': ['Москва']})
                else:
                    route.fulfill(path=root / name)
            page.route('https://miniapp.example.test/**', asset)
            page.goto('https://miniapp.example.test/')
            page.locator('#trip-form[data-ready="true"]').wait_for()
            page.locator('#destination').fill('Сочи')
            page.locator('#budget-amount').fill('270123')
            page.locator('#date-mode').select_option('range')
            page.locator('#nights-mode').select_option('range')
            page.locator('#date').fill('2099-10-15')
            page.locator('#date-to').fill('2099-11-15')  # 32 inclusive days
            page.locator('#nights').fill('7')
            page.locator('#nights-to').fill('10')
            page.locator('#consent').check()
            if surface == 'vk-miniapp':
                page.locator('#terms-accepted').check()
            page.locator('#submit').click()
            pw.expect(page.locator('#review')).to_be_hidden()
            assert not page.locator('#date-to').evaluate('input => input.checkValidity()')
            page.locator('#date-to').fill('2099-11-14')  # 31 inclusive days
            assert page.locator('#date-to').evaluate('input => input.checkValidity()')
            page.locator('#nights-to').fill('6')
            page.locator('#submit').click()
            pw.expect(page.locator('#review')).to_be_hidden()
            page.locator('#nights-to').fill('10')
            page.locator('#submit').click()
            pw.expect(page.locator('#review')).to_be_visible()
            assert '7–10' in page.locator('#summary').inner_text()
            assert '270' in page.locator('#summary').inner_text()
            page.locator('#edit').click()
            assert page.locator('#date-to').input_value() == '2099-11-14'
            assert page.locator('#nights-to').input_value() == '10'
            page.locator('#date-mode').select_option('exact')
            page.locator('#nights-mode').select_option('exact')
            pw.expect(page.locator('#date-to')).to_be_disabled()
            pw.expect(page.locator('#nights-to')).to_be_disabled()
            page.locator('#submit').click()
            pw.expect(page.locator('#review')).to_be_visible()
            text = page.locator('#summary').inner_text()
            assert '7–10' not in text and '14.11.2099' not in text and '2099-11-14' not in text
            assert 'POST' not in requests
            page.locator('#edit').click()
            page.locator('#date-mode').select_option('range')
            page.locator('#nights-mode').select_option('range')
            assert page.locator('#date-to').input_value() == '2099-11-14'
            assert page.locator('#nights-to').input_value() == '10'
            assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
            page.evaluate("document.getElementById('trip-form').reset()")
            pw.expect(page.locator('#date-mode')).to_have_value('exact')
            pw.expect(page.locator('#nights-to')).to_be_disabled()
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
                page.locator('#departure').fill('')
                page.locator('#departure').fill('Архангельск')
                pw.expect(page.locator('#departure-support')).to_be_visible()
                page.get_by_role('button', name='Вылет из: Москва').click()
                assert page.locator('#departure').input_value() == 'Москва'
            else:
                assert page.get_by_role('button', name='Вылет из: Москва').count() == 0
                assert 'проверим при поиске' in page.locator('#departure-support').inner_text()
            assert page.locator('#budget-amount').input_value() == '270123'
            assert 'POST' not in requests
        finally:
            browser.close()
