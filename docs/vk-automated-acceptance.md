# Automated VK acceptance

`VK Automated Acceptance` runs offline dialog, browser and provider contracts
on relevant pull requests and main changes. It retains JUnit results and
synthetic screenshots for 14 days. No production credentials are used.

The dialog scenario drives the real Flask webhook and state machine through
form entry, search, selection, budget edit, replacement search, old-button
rejection, current selection and cancellation. VK, CRM and manager transports
are intercepted; it asserts that no lead is created.

Mini App browser checks run against a loopback Flask server with fictional
signed launch parameters. They cover desktop/mobile layout, validation,
review/edit preservation, local draft round trips and failure/retry behavior.
The new review/edit case forbids all requests outside that local server.

CI uses the Chromium build pinned by Playwright 1.62.0. Bridge host calls are
stubbed locally; explicit pending/rejected host tests replace that stub.
Existing Edge CI remains compatible through the default browser channel.

Run locally after installing dependencies and the test browser (set
`VK_E2E_BROWSER=chromium` to match this workflow):

```sh
python -m pytest tests/test_vk_bot.py tests/test_vk_miniapp_e2e.py \
  tests/test_alean.py tests/test_tour_providers.py tests/test_alean_live_smoke.py
```

## Separate live supplier smoke

On a trusted environment with protected ALEAN_USERNAME/ALEAN_PASSWORD bindings:

```sh
python -m scripts.alean_live_smoke
```

This existing command authenticates GetCountries and performs one fictional
GetTours search without importing either bot or enabling the application.
It returns nonzero on missing credentials or failed search and prints only a
sanitized report. It is intentionally separate from automatic offline CI:
routine commits must not spend supplier quota or require production secrets.

These checks verify our application, not delivery/rendering inside a real VK
client. No alternate browser or MCP is used to bypass a blocked VK origin.
