# REG.RU Cloud AI test runbook

This integration is opt-in and intended for a limited test before any
production rollout.

## Data boundary

Only the already-collected trip parameters `destination`, `dates`, `people`,
and `budget` are passed to the model. Email addresses and phone numbers found
inside those fields are redacted before the request. The prompt must not
contain a name, Telegram/VK identifier, phone, email, passport, payment,
medical, or other personal data.

The normal lead database remains on the Russian VPS. If the external provider
is unavailable or misconfigured, the bot falls back to deterministic local
templates and still completes the lead flow.

This technical minimization does not by itself prove legal compliance. Before
production use, the operator must confirm the current REG.RU Cloud AI contract,
processing location, subprocessors, retention/logging, deletion procedure,
security measures, and whether the privacy notice/consent and Roskomnadzor
notification need updating. REG.RU's token API should not be treated as a
private or local model merely because the provider is Russian.

## Test configuration

Store the key only in `/opt/turbot/.env` (mode `0600`); never commit or print it:

```dotenv
AI_MODE=regcloud
REGCLOUD_API_KEY=<created in the REG.RU Cloud panel>
REGCLOUD_BASE_URL=https://ai.reg.cloud/v1
REGCLOUD_MODEL=gemma-4-26b-a4b-it
```

Keep `AI_CHAT_ENABLED=false`. This test enables only the guarded post-lead tour
blurb. It does not enable free-form AI chat.

Restart the Telegram service, submit a synthetic test request with no personal
data, and verify both the generated blurb and the local template fallback after
temporarily selecting an invalid test key. Do not use a real customer's lead
for the smoke test.
