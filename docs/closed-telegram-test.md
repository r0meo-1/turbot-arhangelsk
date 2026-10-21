# One-account Telegram AI test

`tools/closed_telegram_ai.py` is an isolated integration harness using the shared
AI helper; it is not the production funnel or a new public bot entrypoint.
It never imports bot.py, CRM, supplier, VK or booking code. No lead is created.
The tester also plays the manager role: every send is restricted at the transport
layer to the same positive private-chat/user ID. Other users and all groups are
ignored before inference or replies. Username alone is not used as authorization.

## Private configuration

Keep outside the checkout in a mode-600 file:

```ini
TEST_TELEGRAM_BOT_TOKEN=<dedicated test bot token>
TEST_BOT_USERNAME=<dedicated test bot username>
TEST_USER_ID=<verified private Telegram user ID>
FORBIDDEN_PRODUCTION_BOT_ID=<production bot ID>
```

Use the existing private Yandex config as `--ai-env`. Never copy the production
bot token into the test config. The harness rejects its ID, verifies getMe and
refuses an existing webhook without changing it. Only getMe, getWebhookInfo,
getUpdates and sendMessage are permitted. Preflight without `--run` is read-only.

```bash
python tools/closed_telegram_ai.py --config ../closed-telegram.env \
  --ai-env ../.env --state ../closed-run/state.json
```

## Bounded runtime

Run as `turbot` in a separate sandbox checkout/venv. Recommended transient unit:

```bash
systemd-run --unit=turbot-ai-closed-test-20261021 --uid=turbot \
  --property=RuntimeMaxSec=3600 --property=UMask=0077 \
  --property=NoNewPrivileges=yes --property=ProtectSystem=strict \
  --property=PrivateTmp=yes --property=EnvironmentFile=/etc/turbot/telegram-proxy.env \
  --property='InaccessiblePaths=/opt/turbot /etc/turbot' \
  --property=ReadWritePaths=<sandbox>/closed-run \
  <sandbox>/repo/.venv/bin/python <sandbox>/repo/tools/closed_telegram_ai.py \
  --config <sandbox>/closed-telegram.env --ai-env <sandbox>/.env \
  --state <sandbox>/closed-run/state.json --run
```

This opens no listening ports. Proxy credentials are injected by systemd and not
printed; they do not configure the Yandex HTTP client. Existing production units,
webhook and environment files are unchanged. The loop lasts at most one hour
(systemd enforces the wall-clock limit), with at most 10 attempted inference calls
and 10 manager notices per state file. A Linux file lock prevents two pollers.
Do not launch a second poller or reset a webhook. State binds to bot ID and tester.

## Acceptance and evidence

1. Tester opens the separate bot and sends `/start`.
2. `/ask Что взять в поездку?` exercises the shared provider/guardrails with fixed
   synthetic trip context. `ИИ:` and plain questions are supported too.
3. `/manager` routes a real, clearly marked test manager notice back to the tester.
4. API `ok` plus matching chat/message ID is recorded as `api_accepted` only.
5. Tester sees the notice and sends its `/ack <test-id>` command; only then is
   `human_acknowledged` recorded. `/status` shows aggregate counts.

No raw questions/answers or secrets are persisted in state. It contains offsets,
limits and test-notice receipt metadata. Consumption is persisted before side
effects: replay after restart does not resend, but a crash/network uncertainty
may leave an unconfirmed event. There is no automatic retry or false delivery
claim. A failed client reply does not suppress the separate manager notice attempt.
An unaccepted notice cannot be acknowledged as delivered.

This checks the real Telegram test transport and shared AI helper, not production
CRM routing or delivery to a second person's account. The production webhook
router is covered separately with mocked transports.

## Stop / rollback

`systemctl stop turbot-ai-closed-test-20261021.service` stops only this transient
test. The unit also expires automatically. Preserve the private receipt file for
review. No production configuration or service restart is required. Do not revoke
or remove the bot/key or delete chats without the user's authorization.

## Offline main-router bridge

`--main-router` selects `tools/closed_main_router.py`. It invokes the actual
`bot._process_update`, `_handle_lead_assist`, shared guards and manager routing.
Only synthetic `/ask` or `ИИ:` text reaches this router; `/manager` becomes an
explicit manager question. Other harness controls remain local to the harness.
This is not the production webhook server, callback routing or full lead funnel.

During bot import and router execution, socket connect/DNS and thread start are
blocked, dotenv loading is disabled, bot credentials are dummy, and the database
is a separate file beside the test state. VK delivery and analytics are replaced;
Telegram outputs are captured with an exact-recipient assertion. No network call
from bot.py is permitted. No production configuration is sourced.

For a safe model question the first offline pass captures the actual SDK request.
Only that request is submitted outside the router scope via the bounded Yandex
client. A second offline pass handles the response with the original helper and
output guards. Intermediate outputs are discarded; only the final reply and
handoff decision reach the existing closed Telegram transport. Local router
metrics can therefore count two passes; do not treat them as live production
metrics. Model failure is replayed as a generic provider error, without raw data.

The real marked notice uses the harness transport and receipt/ack mechanism,
not the production VK/TG transport implementation. This verifies the main
handler's decision plus separate real delivery, not production CRM acceptance.
User and transport allowlists, persisted quotas, receipt verification, update
consumption and time limit are inherited. Reuse state to preserve previous
receipts and offset; stop the previous poller before starting this variant.
Never run two pollers or reset a webhook.

## Webhook + sender test

`--webhook-router` selects `tools/closed_webhook.py`. This variant checks the
actual Flask webhook, secret validation, `dispatch_update`/state flush, callback
routing and `send_message`. It opens no port and sets no Telegram webhook.
GetUpdates carries messages and callback queries into an exact-user/chat gate;
callbacks additionally require an owned test-bot message and the `aiq:` prefix.
Admin, funnel, arbitrary callback and other user/group routes are blocked before
bot execution. `/start` creates only a test keyboard, never a lead.

Bot code executes with disabled socket connect/DNS/workers, dummy credentials,
disabled dotenv and separate SQLite databases. A probe pass captures a model
request in `probe.sqlite`, without real sends; a final pass uses `webhook.sqlite`
and the actual update ID for durable webhook receipts. The bounded SDK runs
outside bot execution. Only the intercepted Telegram HTTP call can temporarily
restore sockets to the existing dedicated, exact-recipient transport. VK is
replaced with a no-op. No supplier/CRM/booking/payment route is allowed.

The HTTP interceptor admits sendMessage, callback acknowledgement for an observed
query and keyboard removal in the single test chat. Typing is local only.
Actual API acceptance is matched to chat/message ID. A marked manager notice
includes the private test /ack command. The agent must never send that /ack.

`ai_handoff_receipts` stores event ID, correlation label, timestamp and channel
outcomes, without question, trip context or recipient IDs. Channel slots remain
independent even if privacy correlation is unavailable. Rows are written with
uncertain outcomes before sends, then updated after each response. A matching
Telegram receipt is accepted; explicit API refusal is failed; missing/malformed
receipt or transport exception is uncertain. No configured recipients is
not_configured. An accepted channel does not imply all channels succeeded.
No automatic resend is performed. Review persisted channel outcomes alongside
`ai_handoff_delivery` metrics; webhook complete means handler completion only.
Receipts require an operator retention/reconciliation policy before public use;
this change does not create an automatic retry worker or CRM ticket.

Targeted validation (no live requests):

```bash
python -m pytest tests/test_closed_webhook.py tests/test_closed_main_router.py tests/test_closed_telegram_ai.py
```

Only a separate sandbox may use `--webhook-router --run`; preserve state/offset,
stop any previous poller first, use the same restrictive systemd properties,
point ReadWritePaths at its dedicated state directory, and set a short runtime.
Stop only that transient test unit after acceptance. Never use production env,
token, database or service. The bot identity/webhook preflight still applies.
