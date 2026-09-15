# VK Mini App browser E2E

`test_vk_miniapp_e2e.py` runs the full browser roundtrip in **Edge Bot CI** because that workflow installs Playwright and Microsoft Edge.

The regular pytest/deploy workflow intentionally does not install browser tooling. There the module uses `pytest.importorskip("playwright.sync_api")`, so the browser-only test is skipped while all ordinary backend tests still run.

The Edge test covers signed VK launch parameters, form review, `POST /vk/miniapp/draft`, normalized draft persistence, and the return link to the VK community chat. It uses a test-only signing secret and never reads production VK secrets.
