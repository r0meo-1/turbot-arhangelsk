import json
from pathlib import Path

import pytest

pw_api = pytest.importorskip("playwright.sync_api")
EXT = Path(__file__).resolve().parents[1] / "agent-extension"


@pytest.mark.parametrize("status", [200, 401, 503])
@pytest.mark.parametrize("action", ["clear", "replace"])
def test_pairing_change_ignores_pending_connection_response(tmp_path, status, action):
    with pw_api.sync_playwright() as pw:
        context = pw.chromium.launch_persistent_context(
            str(tmp_path / "profile"), channel="chromium", headless=True,
            args=[f"--disable-extensions-except={EXT}", f"--load-extension={EXT}"],
        )
        try:
            context.route("https://**/*", lambda route: route.abort())
            pending = []
            context.route("https://bot.r0meo1.ru/agent-extension/**", lambda route: pending.append(route))
            worker = context.service_workers[0] if context.service_workers else context.wait_for_event("serviceworker")
            page = context.new_page()
            page.goto(f"chrome-extension://{worker.url.split('/')[2]}/sidepanel.html")
            pw_api.expect(page.locator("#connectionState")).to_have_text("Не подключено")
            page.locator("#name").fill("QA LOCAL DRAFT")
            page.locator("#token").evaluate("el => el.closest('details').open = true")
            page.locator("#token").fill("synthetic-not-a-live-credential")
            page.evaluate("""() => {
              const refresh = refreshConnectionState;
              refreshConnectionState = () => {
                window.pendingRefresh = refresh();
                return window.pendingRefresh;
              };
            }""")
            page.locator("#saveToken").click()
            for _ in range(100):
                if len(pending) == 2:
                    break
                page.wait_for_timeout(20)
            assert len(pending) == 2
            page.evaluate("() => { window.oldRefresh = window.pendingRefresh; }")
            if action == "clear":
                page.locator("#clearToken").click()
                expected = "Не подключено"
            else:
                page.locator("#token").fill("synthetic-replacement-token")
                page.locator("#saveToken").click()
                for _ in range(100):
                    if len(pending) == 4:
                        break
                    page.wait_for_timeout(20)
                assert len(pending) == 4
                for route in pending[2:]:
                    route.fulfill(status=200, content_type="application/json",
                                  body='{"ok":true,"leads":[],"tasks":[]}')
                page.evaluate("async () => { await window.pendingRefresh; }")
                expected = "Подключено"
            pw_api.expect(page.locator("#connectionState")).to_have_text(expected)
            for route in pending[:2]:
                assert route.request.method == "GET"
                route.fulfill(status=status, content_type="application/json", body=json.dumps({
                    "ok": status == 200, "leads": [{"id": 99, "name": "QA STALE LEAD"}],
                    "tasks": [], "error": "synthetic error",
                }))
            # Drain the actual pending promises, not merely a UI snapshot taken
            # before the responses can arrive.
            page.evaluate("async () => { await window.oldRefresh; }")
            pw_api.expect(page.locator("#connectionState")).to_have_text(expected)
            pw_api.expect(page.locator("#name")).to_have_value("QA LOCAL DRAFT")
            assert "QA STALE LEAD" not in page.locator("body").inner_text()
            token = page.evaluate("async () => (await chrome.storage.local.get('agentToken')).agentToken")
            assert token == (None if action == "clear" else "synthetic-replacement-token")
        finally:
            context.close()
