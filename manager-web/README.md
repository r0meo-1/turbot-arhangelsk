# Mobile manager journal (work in progress)

## Architecture and live-validation boundary

This directory contains the browser shell and owner-controlled local tooling.
The browser shell is allowed to display synthetic demo data and, after a
manager manually enters an Agent Desk pairing key, use the existing protected
CRM routes. It must never contain Telegram bot credentials, private keys, or a
live-field-testing result that has not actually happened.

The implementation is intentionally split into three layers:

| Layer | Location | Responsibility | External writes |
| --- | --- | --- | --- |
| Browser shell | `index.html`, `app.js`, `styles.css` | Render manager UI and read-only demo | None in demo mode |
| Synthetic fixtures | `../tests/fixtures/` | Deterministic local contracts | None |
| Owner tools | `owner-session.js`, `live-validation.js` | Manual transition to a real check | Only after explicit owner invocation |

`owner-session.js` and `live-validation.js` are deliberately excluded from
the Flask static-asset allowlist. They run in an owner-controlled local Node
process, never in the browser.

### Synthetic fixture isolation

`tests/fixtures/manager_demo.json` is the expected rendering contract for the
manager demo. It contains fictional Cyprus travel data and is consumed only by
the local browser test. `tests/fixtures/application_status.json` is a separate
local status contract for the external application workflow:

```json
{
  "source": "local-fixture",
  "status": "draft",
  "submitted": false,
  "employer_response": null
}
```

These fixtures are not a cache, inbox, CRM export, or employer API response.
Tests use a local Flask server and synthetic browser data; they do not call
Turbot, Habr, an employer, Telegram, or an iPhone. A fixture may never be
promoted to `submitted`, `accepted`, `rejected`, or an employer response as a
side effect of a test.

### Real-world checks are structural placeholders

The current status of the real dependencies is intentionally explicit:

- iPhone verification: `not-verified` / waiting for an owner session;
- private-key verification: `not-provided` until manually entered locally;
- external application: `draft`, `submitted: false`;
- employer response: `null` until a real response is manually observed and
  recorded through an approved workflow.

`live-validation.js` exposes hooks that can emit an
`awaiting-owner-session` event for these checks. The hook receives only
sanitized status and never receives the private key. It does not claim that a
field check occurred and does not submit an application.

The interactive lifecycle is bounded and one-shot:

1. `requestManualCheck()` creates a non-secret request id and emits the
   `awaiting-owner-session` event.
2. `waitForManualInput()` waits for a matching request and returns an explicit
   `manual-input-timeout` if no owner input arrives before the deadline.
3. `submitManualInput()` accepts input only for an actively awaited request.
   It exposes a temporary `Buffer` only to the registered consumer and clears
   that buffer in `finally`; the returned lifecycle result never contains the
   input.

The isolated verification script uses a synthetic value and does not contact
any service:

```powershell
node tools/verify_manual_session.js
```

Its output reports only the request id, lifecycle phase, check name, and
consumed byte count. Replace its synthetic adapter with a human-controlled
terminal/UI adapter only during an explicitly authorized field session.

### Manual live transition

The only supported transition is:

```text
mock fixture
  -> explicit owner command
  -> hidden local key prompt
  -> configured live connector
  -> sanitized in-memory result
```

Run the safe default with:

```powershell
node manager-web/owner-session.js
```

Live mode requires an explicit flag, a privileged HTTPS verification endpoint,
and an exact host allowlist. The connector performs a `POST` and sends the
owner key in its `Authorization` header, so this endpoint is not a read-only
health probe and must be controlled by the owner. URL credentials, HTTP,
subdomain/suffix matches, and unlisted ports fail closed before any request:

```powershell
$env:OWNER_SESSION_ENDPOINT = "https://owner-controlled-endpoint.example/check"
$env:OWNER_SESSION_ALLOWED_HOSTS = "owner-controlled-endpoint.example"
node manager-web/owner-session.js --live --confirm-live
```

The key is never placed in JSON fixtures, command arguments, browser storage,
source control, or application logs. The endpoint is not configured in CI.
No live command is run by automated tests.

### Staging safety gate and readiness probe

The live-validation hooks require the exact environment value
`LUNA_ALLOW_LIVE_STAGING=true`. Missing, empty, or any other value forces
`mock-fallback` to the synthetic fixture. The browser cannot enable this gate.
The hooks and readiness probe share one sanitized runtime parser: malformed
health URLs also fail closed to mock, while a valid opt-in with no health URLs
reports an incomplete `live-staging` configuration without making a request.

The non-destructive pre-flight command is:

```powershell
node tools/check_staging_readiness.js
```

It reads comma-separated `STAGING_HEALTH_ENDPOINTS` and, only when the gate is
open, sends unauthenticated `HEAD` requests with a bounded timeout. It never
uses `OWNER_SESSION_ENDPOINT`, sends a request body, submits an application,
or transmits a private key. With the default closed gate it reports
`networkAttempted: false`.

The website Flask app serves `/manager/`. This interface uses the existing CRM
endpoints and database. It does not create a second customer store. Inside a
Telegram Mini App it sends the short-lived, signed `Telegram.WebApp.initData`
with every request; the server validates the HMAC and accepts only `ADMIN_ID`
or a user listed in `MANAGER_TELEGRAM_IDS`. Manual Agent Desk bearer entry
remains available as a fallback. The journal currently supports a privacy-safe
24-hour aggregate summary, the today/overdue queue, request history, adding a
note, and scheduling a next task in the device's timezone. Dashboard SLA is
explicitly system create-to-manager-delivery latency, not human response time.
An authenticated manager may atomically claim an unassigned request. The API
is idempotent for the same manager and returns a conflict instead of silently
overwriting another manager's assignment.
The assigned manager may release their own request. `ADMIN_ID` may release any
request for operational recovery; another allowlisted manager receives 403 and
cannot clear someone else's assignment. Release uses a compare-and-update guard
so a concurrent assignment change is not silently overwritten.
The request card also renders the same server-side first-reply template used in
Telegram/VK lead notifications. The browser may copy it to the local clipboard
but never sends a customer message; the manager reviews the text and chooses
the destination channel manually.

The public HTML contains no CRM data. The user supplies an existing Agent Desk
pairing key; it is held in memory and cleared on logout/pagehide. No persistent
browser token storage, external scripts, or customer-message sends are used.
This key has the existing shared Agent Desk privileges, not per-manager roles.

## Secure local-owner workflow

`BOT_TOKEN`, `ADMIN_ID`, `MANAGER_TELEGRAM_IDS`, and
`AGENT_EXTENSION_TOKEN` are server-only settings.
They must be supplied through the service environment or an owner-only `.env`
file; none is embedded in `manager-web/`, sent to the browser, or stored in
browser storage. `BOT_TOKEN` and `ADMIN_ID` enable `/agentdesk`; the optional
`AGENT_EXTENSION_TOKEN` overrides the derived pairing key. Restart the service
after changing any of them and retrieve the pairing key through the owner
Telegram session. Paste it manually into the local manager page, and use
logout/page reload to clear it. Do not automate key entry or submit an external
application from tests.

The local application-status fixture at
`tests/fixtures/application_status.json` is intentionally `draft` with
`submitted: false`; it is not an employer response and does not call TurBot,
Habr, or any external vacancy service.

`owner-session.js` is intentionally not a served manager asset. Run it only
from an owner-controlled terminal. Its default command prints the synthetic
state. Live verification requires both `--live` and `--confirm-live`, plus a
server-side `OWNER_SESSION_ENDPOINT` and exact `OWNER_SESSION_ALLOWED_HOSTS`;
the private-key prompt is hidden, and the key is passed only to the injected
live connector and then discarded. A
live response updates only the in-memory status returned by that process. It
does not rewrite `application_status.json` or create a browser session.

Writes are never automatically retried: a network error can occur after a
successful server commit. The interface asks the manager to inspect history
before retrying. Buttons disable while their operation is pending.

Browser smoke on localhost with an isolated in-memory synthetic API passed: valid/invalid key, queue, request history, note persistence across reconnect, task creation with local time, logout clearing the visible data. At 375px viewport width, no horizontal overflow. This is Chromium evidence, not Safari or production database validation.

Remaining before release: actual iPhone verification, task completion, Telegram admin identity, deployment packaging review, CI and production revision checks.
Do not claim issue #209 is complete or that this version has been deployed.

Validation so far: `python -m pytest tests/test_manager_web.py
tests/test_website_app.py -q --basetemp=<fresh-local-directory>`: 38 passed.
This covers server routes and existing CRM operations, not browser interaction.

Synthetic browser check also passed creating a quote (150000 RUB) and recording a reaction that remained selected after reopening the card. No real offers or customer data were used.
