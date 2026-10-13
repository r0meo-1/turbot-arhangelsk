# Alean in the Telegram bot and Mini App

## What is implemented

The main Python TourBot/TurBot (`@apreltour_bot`) has an independent, read-only
`/alean` conversation and an “Алеан: отели и туры • демо” destination button.
The Telegram command menu includes `/alean`. The Mini App has a visible Alean
entry card, linking to `alean.html`. Neither flow enters the lead/contact/CRM
pipeline. The legacy lead form remains a separate production feature.

Both surfaces support domestic **hotel** and **hotel-plus-flight tour** demos:
Sochi, Adler, Anapa, Gelendzhik, Kislovodsk, or Russia; arrival/check-in and
departure/check-out dates; 1–8 adults; up to six children with ages 0–17;
a total RUB budget; and departure city for packages. Stays must be 1–28 nights
and cannot start in the past. Budget zero means no limit. Searches, review,
loading, empty results, validation errors, timeout, supplier error, retry,
Back, parameter edits, and Cancel are implemented. Back retains answers.
Cancel cancels the current search; the Mini App retains editable form values,
while Telegram clears its read-only conversation. Old Telegram buttons and
late search responses cannot publish into a cancelled or edited conversation.

Results show a name, location, dates, total price, currency, and an availability
label. **Every demo hotel name, price, and availability is fictional**, clearly
labelled as such. They are not Alean inventory. No fixtures masquerade as live
data. Live results are always indicative and `confirmed=false`.

`booking_url` is explicitly null. The current SAPI adapter has no verified
official offer booking URL, and its local `alean:` identifiers are not supplier
booking tokens. No booking links are invented. Booking, payment, and submission
controls are disabled in the search UI, including live mode. There are no
booking/payment/manager APIs in this read-only contract.

## Safe local demo

```sh
python -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python tools/run_alean_demo.py --port 8765
```

Open:

- `http://127.0.0.1:8765/miniapp/` — Mini App entry card.
- `http://127.0.0.1:8765/miniapp/alean/alean.html` — search screen.
- `http://127.0.0.1:8765/demo/telegram` — local Telegram transport preview.

The preview drives the **real bot dispatch/message/callback handlers**, but
captures messages locally instead of sending them to Telegram. It uses a
fictional user, a temporary SQLite database, a dummy bot token, and a loopback
listener. External requests and lead/manager transport are blocked. No real
customer data, CRM leads, supplier searches, bookings, payments, or real
customer/manager messages are created. The page states that it is **not
Telegram Web**. It is evidence about handler/UI behavior, not live delivery.

The Mini App’s “Демо-сценарий” selector exercises results, no results, provider
timeout, and provider error without any supplier traffic. The Telegram preview
has the same selector. These test controls are not offered in live Mini App mode.

## Telegram deployment and test-only configuration

The search screen can be served by `bot.py` at `/miniapp/alean/alean.html`
or by the GitHub Pages copy in `docs/miniapp/alean.html`. The source and Pages
assets have a synchronization test. When served by the bot, search uses the
same-origin `/miniapp/alean/search` endpoint. GitHub Pages uses the existing
production backend host. Demo requests do not contain Telegram initData,
contacts, customer names, phones, or messenger IDs.

For a **separate Telegram demo instance**, configure server-side:

```dotenv
ALEAN_DEMO_ONLY=true
ALEAN_READONLY_LIVE_ENABLED=false
ALEAN_ENABLED=false
MDT_ENABLED=false
TUTU_ENABLED=false
```

`ALEAN_DEMO_ONLY` blocks normal Telegram completion and both Mini App submission
transports, not just the search UI. `/start` on this instance opens the Alean
demo; unrelated lead buttons cannot submit a request. A deployment restart is
required after changing environment settings. Existing `DEMO_MODE` alone is
insufficient for the “no local leads” requirement: it historically masks phone
data and suppresses forwarding but still saves local leads.

Do not send test `/start` or legacy form submissions to the production bot
without verifying this dedicated read-only setting. `/alean` itself is always
the safe, labelled demo and does not enter the lead pipeline. Configuration
and customer activation were **not** changed by this implementation.

## Live search prerequisites and known limits

The existing adapter is `shared/alean.py`. Its documented SAPI endpoint is
`https://sapi.alean.ru:3443/services`; it allows only catalog GET operations and
`GetTours`. Existing package search is reused rather than creating a guessed
API. See [the existing integration contract](alean-integration.md).

Official sources inspected:

- [Alean’s full API vs Search-API comparison](https://www.alean.ru/page/ta-api/?label=diff)
  (HTTP 200 checked on 13 October 2026). It distinguishes search from SOAP’s
  broader product/booking functionality, says an agency contract/access request
  is needed, and describes SAPI IP authorization. This public page does not
  establish that a particular account, IP, or HTTPS SOAP test route is authorized.
- The adapter refers to the account-specific `AleanSearchAPI.DOCX`, SAPI 2.3,
  supplied on 7 August 2026. That document is not attached to this checkout.
  Historical SAPI checks are documented separately; they are not current live
  verification of this new UI.

The current cloud has `ALEAN_PASSWORD` but **no `ALEAN_USERNAME`**. A request to
SAPI without credentials returned HTTP 401 in this cloud. This verifies HTTP
reachability, not authenticated search. SOAP test username/password variables
are present but do not substitute for dedicated SAPI credentials. No credential
values were printed or added to source, screenshots, Telegram messages, or JS.

To enable read-only live **package** searches, obtain/verify the dedicated SAPI
username, account permissions, and IP authorization with Alean, then configure:

```dotenv
ALEAN_DEMO_ONLY=false
ALEAN_ENABLED=true
ALEAN_USERNAME=<dedicated SAPI username>
ALEAN_PASSWORD=<server-side secret>
ALEAN_READONLY_LIVE_ENABLED=true
```

Run the existing read-only account smoke check first:

```sh
.venv/bin/python -m scripts.alean_live_smoke
```

Then `/alean_live` can use live packages. The Mini App’s live mode additionally
requires server-verified Telegram initData; plain browser previews cannot call
the authenticated path. Failed live searches do **not** silently turn into mock
results. Contacts/identity are removed before the supplier call. At most two
Mini App searches run simultaneously. Secrets remain in the server environment.

**Live hotel-only search is intentionally unavailable** until the current Alean
contract and authenticated hotel-only request/response are verified. The old
adapter only requests hotel-plus-flight packages. The UI explicitly explains
this limit; hotel-only demos remain fully testable.

SOAP `SpecifyTourPackageReservation` requires a verified HTTPS test route,
successful authenticated session, and supplier package/transport XML. Prior
testing returned `lrAccessDenied`. No booking token is derived from a local
card ID, and no SOAP endpoint or request schema is invented here. Provide the
current account documentation, working HTTPS test route and account access,
and an authenticated package/Specify contract test before implementing price
confirmation. Live availability and official booking links remain unverified.

## Evidence and tests

```sh
.venv/bin/python -m pytest tests/test_alean_readonly.py tests/test_telegram_alean.py \
  tests/test_miniapp_route.py tests/test_telegram_miniapp_pages_sync.py
.venv/bin/python -m pip install playwright==1.62.0
.venv/bin/python -m playwright install chromium
ALEAN_BROWSER_PATH=/path/to/chromium \
ALEAN_SCREENSHOTS_DIR=docs/screenshots/alean \
  .venv/bin/python -m pytest tests/test_alean_demo_browser.py
```

Browser tests own an isolated demo subprocess and block all external browser
requests. CI’s Edge Bot job runs them and uploads screenshots. Cases exercise
the entry card, Sochi package search, child ages, changed parameters, Adler hotel
search, budget filtering/no results, disabled booking, Back/Cancel, invalid dates
and ages, provider timeout/error and recovery, and browser request timeout.
The real Telegram handlers are exercised through the local preview, including
provider failures, stale callbacks, edits, cancellation, and zero-lead checks.

Captured browser evidence (all **local demo**, not signed-in Telegram Web):

Cloud validation on 13 October 2026: 1,489 regression tests passed (existing
browser-only suites are delegated to the Edge Bot job); the final Alean-specific
suite passed 34 backend/handler cases and 7 browser cases. Resource-warning and
secret-scanning checks passed 49 cases. The screenshots below were generated from
the isolated browser run. The initial browser check found a missing stable
destination label, which was corrected before the final passing run.

- [Mini App search form](screenshots/alean/miniapp-search.png)
- [Visible Mini App entry card](screenshots/alean/miniapp-entry.png)
- [Loading](screenshots/alean/miniapp-loading.png)
- [Sochi results](screenshots/alean/miniapp-results.png)
- [No results](screenshots/alean/miniapp-empty.png)
- [Validation](screenshots/alean/miniapp-validation.png)
- [Provider timeout](screenshots/alean/miniapp-timeout.png)
- [Provider error](screenshots/alean/miniapp-error.png)
- [Real Telegram handler results in local preview](screenshots/alean/telegram-local-results.png)
- [Telegram handler provider error](screenshots/alean/telegram-local-error.png)

This cloud has Chromium/Playwright but **no authenticated Telegram Web browser
session or in-app browser-control tool**. Therefore actual Telegram Web delivery
and `@apreltour_bot`’s deployed UI have not been verified. The latest deployments
failed on SSH connection timeouts, and the public Telegram health URL returned
503 during prior diagnostics. No production deployment or real-message test is
claimed. Once server connectivity and browser access are restored, run the same
fictional Sochi/Adler, Back/Cancel and failure cases on the dedicated read-only
demo instance, without requesting a contact or submitting a legacy lead form.
