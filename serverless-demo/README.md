# АПРЕЛЬ тур: Telegram Serverless demo

Standalone JavaScript port of the Telegram tour-request **demo**. This folder
does not replace the Python/VK services, modify their SQLite database, or deploy
the existing Mini App. Runtime imports are limited to the official `sdk` and
local JavaScript modules.

## Behaviour

- Destination → departure city → dates → nights → adults → child ages → budget
  → demo contact → review → **Complete demo**.
- Every answer can be edited, including nights. Calendar-month arithmetic clamps
  month-end dates; the review displays the actual departure and return dates.
- A completed demo can be reopened with **Edit**. Flexible dates can be replaced
  with exact dates without restarting the questionnaire; other answers stay saved.
  Review and complete the demo again before explicitly searching Tutu.
- Completion does not create a production lead, contact a manager, call CRM,
  generate an AI recommendation, book travel, or charge anything.
- An explicit button after completion searches Tutu for real round-trip fares.
  Only route/date/passenger-band/count/budget parameters leave the bot. Tutu
  errors produce a visible unavailable response, never invented prices.
- Supported date text: `YYYY-MM-DD`, `DD.MM.YYYY`, `15 июня`, `15–22 июня` and
  the displayed presets. Cross-month natural-language ranges are not supported.
- Country presets resolve to the same resorts as the source bot (Turkey →
  Antalya, Egypt → Hurghada, etc.); the returned route is shown explicitly.
- `/start`, `/help`, `/privacy`, `/cancel`, `/delete`. Private chats only.

## Data and concurrency

`aprel_demo_sessions` stores a chat ID, monotonically unique update revision,
draft JSON, and expiry time. No names, usernames, full phones, VK addresses,
raw updates, production leads, or bot tokens are stored in the table. Phone
masking happens before database writes. `/delete` clears draft data, retaining
only temporary delivery/deduplication metadata and chat ID.

Drafts expire after 24 hours of inactivity. Expired rows are removed on the next
incoming update; there is no claimed background scheduler. Old callback buttons
are bound to their exact update revision. SQL compare-and-swap protects against
concurrent state changes; up to 64 update IDs plus the last message ID prevent
replay. The outgoing prompt is recoverable after a send failure. An in-progress
send holds a 60-second lease; expired delivery can be retried.

The Bot API has no client idempotency key: a network failure after Telegram has
accepted a send can still duplicate a prompt. It cannot duplicate a production
lead, since this demo never creates one. Tutu requests are user-initiated and
awaited inside the invocation; a platform timeout or crash may require another
tap. No unsupported timer or background thread is used.

## Local checks

Use Node 24+ for the tests (`node:sqlite`), Node 18+ for the CLI:

```sh
cd serverless-demo
npm install
npm test
npx tgcloud status
```

Tests use real in-memory SQLite and stubbed Telegram/Tutu transports. They cover
the full funnel, validation, edit/cancel/delete, privacy, stale callbacks,
concurrent duplicate updates, completion, send-failure recovery and expiry.
They do not establish compatibility with the hosted SDK until the cloud smoke
test has run.

## Deployment to the demo bot

Official reference: https://core.telegram.org/bots/serverless

1. Keep the existing Python service running for VK and any existing Mini App.
   Record the current Telegram webhook configuration securely for rollback.
   This demo changes only the Telegram update handler when deployed.
2. In BotFather, select the intended demo bot → Serverless → CLI Access. Use its
   **CLI access token**, not the regular Bot API token. Never commit either.
3. `npx tgcloud login` links this folder. Credentials are managed by the CLI
   inside gitignored `.tgcloud/`; do not read or edit that directory by hand.
4. Inspect remote contents with `npx tgcloud fetch` and `npx tgcloud diff`.
   Preserve any existing modules. Do not run an untargeted full push against an
   existing project: it can remove remote modules absent from this folder.
5. Deploy only the new schema first: `npx tgcloud push tgcloud/schema.js`.
   If the remote schema already defines other tables, merge the additive demo
   table into it before this step. Run `npx tgcloud migrate --dry-run`; confirm
   that only the new demo table is added, then `npx tgcloud migrate --safe`.
6. Run the read-only hosted smoke test with
   `npx tgcloud run endpoints/cloudCheck '{}'` before changing message delivery.
   It checks the actual bot identity, SDK database access, and demo logic without
   sending messages or writing data. Then deploy libraries and handlers together:
   `npx tgcloud push tgcloud/lib/ tgcloud/handlers/`.
7. Inspect `npx tgcloud webhook`. The CLI refreshes the platform webhook on push;
   verify the URL and update types before calling `webhook sync` if necessary.
   Never discard pending updates automatically.
8. In Telegram run `/start`, complete the synthetic demo, edit nights, cancel,
   exercise invalid values, and `/delete`. Verify completion explicitly says no
   manager/CRM submission. Test the optional Tutu button independently.

If the cloud smoke test fails, stop the cutover and restore the prior webhook
through the existing deployment procedure. No production SQLite migration is
part of this project. The Mini App remains on its existing backend and is not
covered by this chat-demo port.

## Validation recorded during implementation

- 16 local tests passed on Node 24.19.0.
- Official `@tgcloud/cli` 0.2.0 recognizes the module layout.
- Tutu `tools/list` returned HTTP 200 and confirmed `search_avia` parameters.
- A synthetic Moscow–Antalya search exercised the unavailable-response path
  under a 20-second local HTTP deadline; live fare success was not verified.
- Hosted read-only smoke test passed against `@apreltour_bot` (ID 8886586430).
- Deployed on 2026-10-10 with CLI 0.2.0: one additive table migration and
  targeted library/handler push. Webhook sync enabled `message` and
  `callback_query`; no prior Serverless modules or webhook were present.
- Live Telegram flow passed: invalid nights rejected; calendar-month preset;
  phone masking; edit 7 to 10 nights with return-date recalculation; completion
  explicitly confirms no submission; `/delete` clears the synthetic draft.
- Live Tutu button returned the explicit unavailable response. Successful fare
  retrieval remains unverified.
