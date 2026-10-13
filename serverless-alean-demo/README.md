# Read-only Alean demo on Telegram Serverless

This is the native JavaScript port for the designated demo bot `@apreltour_bot`
(bot ID `8886586430`). A VPS is not required. Python/Flask is not deployed to
Telegram's V8 runtime. This project uses only the official `sdk` and `sdk/db`
runtime imports and the documented CLI/static hosting contract.

## Safety contract

`tgcloud/lib/aleanConfig.js` has an immutable **`ALEAN_DEMO_ONLY=true`** setting.
Telegram Serverless does not document Python-style environment variables, so
this is a server-side deployment constant, not a claimed environment binding.
The guard fails closed unless live Alean, supplier calls, bookings, payments,
submissions, CRM leads and manager notifications are all false. No supplier or
CRM credentials are deployed or needed.

Both deployed update handlers import only the guarded Alean runtime. The old
flight/contact/completion handlers are disconnected. Contacts and
`web_app_data` are rejected without saving their content; stale/legacy callback
buttons cannot complete requests. No submit/booking/payment endpoint exists in
this port. The browser disables live mode and calls only `aleanSearch` using
`Telegram.WebApp.Serverless.call`. This preserves platform verification of
Telegram initData; unsigned direct calls remain unauthorized.

Inventory, availability and prices are explicitly fictional. Booking URLs are
null, and booking/submission controls are disabled. The only database writes
are expiring demo dialog drafts in the new `alean_readonly_demo_sessions`
table. The previous demo table declaration is preserved without modification.
Do not enter real contacts, names or customer information in the demo.

## Local verification and build

Node 24 is needed for the SQLite-backed contracts. No npm dependencies:

```sh
npm --prefix serverless-alean-demo test
npm --prefix serverless-alean-demo run build
.venv/bin/python -m pytest tests/test_alean_demo_browser.py tests/test_telegram_miniapp_pages_sync.py
```

The build copies exactly the shared Alean HTML/JS/CSS source assets. Only
`alean.html` (also served as `index.html`), `alean.js`, `alean.css`, `styles.css`,
and `release.json` are hosted; the legacy request form is absent. `release.json`
contains the full commit SHA, safe configuration and SHA-256 asset hashes.
In an isolated archive, set `ALEAN_RELEASE_SHA=<verified PR head>` and
`ALEAN_STAMP_RELEASE=true` to stamp that same SHA into `aleanRelease.js`.
This metadata-only transformation is the only release source modification.

## Deployment procedure

Use the official `@tgcloud/cli` (verified version 0.2.0) and an injected CLI
token. Never read or edit `.tgcloud/`, use `--debug`, print tokens, or use
`push --force`. Do not merge the PR to deploy this project.

1. Verify the full PR head and all applicable CI checks. Archive that commit
   into an isolated directory, build and stamp the release there.
2. Use official `tgcloud pull` in a fresh staging project. Preserve the pulled
   module source, current cloud revision, static configuration, bot menu and
   command configuration for rollback. Never copy `.tgcloud/` state.
3. Overlay only this project's new Alean modules and schema. Preserve other
   pulled modules. Temporarily omit static hosting from staging configuration.
   Push only `tgcloud/schema.js`; inspect `migrate --dry-run`, then `migrate
   --safe`. The allowed change is adding the new demo table; no drops or
   modifications to previous tables are needed.
4. Run `tgcloud run endpoints/aleanStatus '{}'` as a read-only hosted preflight.
   It checks the bot identity, database/table readiness, demo-only flags,
   search/failure/cancel logic and `/alean` route without sending messages or
   creating drafts. CLI `run` executes supplied local code in the hosted SDK;
   it alone does **not** prove the deployed code matches.
5. Copy the built site into staging and enable the committed static config.
   Push the new `lib/alean*.js`, endpoints and both update handlers together
   with the static assets. CLI `push` publishes them in one batch and maintains
   the webhook. Keep the revision concurrency guard enabled.
6. Pull into another fresh project and compare deployed module bytes with the
   release archive (including the SHA stamp). Fetch hosted `release.json` and
   all assets and compare their hashes. Verify unsigned endpoint calls return
   401; legacy submission paths cannot create requests. Run the hosted health
   probe against the verified source and inspect `tgcloud webhook` plus Bot
   API `getWebhookInfo` for the matching handlers and delivery errors.
7. Only after these checks, point the bot's Web App menu at the exact Mini App
   URL printed by the CLI. Keep the previous menu configuration for rollback.

Official platform references are included in the CLI's generated
`AGENTS.md`, `docs/tgcloud-sdk.md` and its 0.2.0 changelog. Supplier prerequisites
and official Alean documentation are in
[the integration notes](../docs/alean-readonly-demo.md).

## Telegram Web acceptance

Open `https://web.telegram.org/k/#@apreltour_bot` while signed in. Send `/alean`.
The reply must explicitly say **ДЕМО**, describe fictional inventory and ask
what to search; the old “Для демонстрации нажмите /start” reply is a failure.
Choose a hotel or tour, Сочи, future arrival, departure seven days later,
two adults, fictional child age `7`, budget `150000` and Москва for a tour.
Check review, Back/keep answer and Search. Results must be labelled fictional
and cannot be booked or submitted. Change to Адлер; exercise empty results,
timeout, error and retry using demo scenario buttons, then Cancel.

Open **Алеан Mini App** from the conversation or menu. Check the same search,
validation (child age `18`, departure equal to arrival), Back preserving
answers, Cancel, all scenario selector values and retry. Booking/submission
buttons must stay disabled. An ordinary browser can load the public screen,
but searching requires Telegram's authenticated SDK. Local SDK stubs and CLI
probes are not authenticated Telegram Web acceptance; report that separately.

## Rollback

Fetch the current official CLI snapshot before rolling back, retaining the
concurrency check. Restore the exact backed-up `handlers/message.js` and
`handlers/callback_query.js` and previous static configuration (use
`static:false` only if the baseline had no site); targeted-push these handlers
and configuration, then restore the saved Bot API Web App menu/commands.
Verify the webhook and previous handler behavior. Leave the additive unused
demo table intact: rollback does not need a destructive migration. Preserved
legacy library modules remain available for restoring the original handlers.
New Alean endpoints are read-only and harmless if left unused; remove them
only through a deliberate reviewed deployment. Rollback restores the
previous demo's behavior, so Alean-only safeguards no longer govern that old
flow after rollback.
