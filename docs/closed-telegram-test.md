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
