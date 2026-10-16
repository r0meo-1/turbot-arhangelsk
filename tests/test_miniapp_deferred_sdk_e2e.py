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
