# REG.RU Cloud AI provider

TurBot can use REG.RU Cloud's OpenAI-compatible API for two guarded customer
flows:

1. the short structured destination blurb generated after a lead is durably
   saved;
2. the Telegram post-lead assistant invoked through `/ask`, the `ИИ:` prefix,
   or whitelisted quick-action buttons shown after lead acceptance.

The customer-facing lead assistant is separate from the older unrestricted
internal `/ai` beta. That internal beta remains behind its existing
Groq-specific beta/ZDR gates. Enabling the post-lead assistant does not enable
the internal beta.

The post-lead assistant receives only minimized trip context needed to answer
the question. Names, phone numbers, usernames and CRM identifiers are excluded.
Restricted topics and unverified commercial facts continue to route to a human
manager or deterministic fallback instead of being asserted by the model.

## Server configuration

The production systemd units read:

```text
/opt/turbot/.env
```

Set the exact values shown by the REG.RU Cloud AI/API-key UI:

```ini
AI_MODE=regcloud
REGCLOUD_API_KEY=<server-only secret>
REGCLOUD_BASE_URL=<exact OpenAI-compatible API base URL>
REGCLOUD_MODEL=<exact model ID>
AI_LEAD_ASSIST_ENABLED=true
```

Do not infer the base URL or model ID from the human-readable model label in the
Sandbox. The code intentionally requires all three values and refuses non-HTTPS
base URLs.

Keep the file private:

```bash
sudo chown turbot:turbot /opt/turbot/.env
sudo chmod 600 /opt/turbot/.env
```

Production deployment applies `AI_LEAD_ASSIST_ENABLED` through the restricted
deploy marker `TURBOT_AI_LEAD_ASSIST_CONFIG_V1`. The marker accepts only
`true` or `false`, updates the server environment atomically, restarts only
the Telegram service, checks local `/health`, and when enabling requires both
`ai_selection.ready=true` and
`ai_selection.lead_assist_enabled=true`.

After deployment/configuration:

```bash
sudo systemctl restart turbot vk-turbot
sudo systemctl --no-pager --full status turbot vk-turbot
curl -fsS https://bot.r0meo1.ru/health
sudo journalctl -u turbot -u vk-turbot -n 100 --no-pager
```

Never paste the API key into GitHub issues, screenshots, logs, or chat messages.

## Failure behavior

- `AI_MODE=template` remains the default.
- Missing key/base URL/model means no external REG.RU call is attempted.
- A non-HTTPS base URL is rejected.
- Provider errors/empty responses fall back to the deterministic local template.
- Lead persistence/manager delivery does not depend on the AI provider.
- Lead-assist questions use the existing privacy-minimized saved-trip context.
- Legal/contract/refund/visa/entry/health/safety/insurance disputes require
  verified information or manager handoff.
- The model must not invent prices, availability, hotel/flight inventory,
  booking status or contractual guarantees.
- Quick-action callback payloads contain only fixed whitelist keys, not arbitrary
  user-supplied prompts.

## Rollback

To disable only customer post-lead AI while keeping the structured REG.RU blurb,
apply the same restricted deploy marker with `false`:

```text
TURBOT_AI_LEAD_ASSIST_CONFIG_V1 false
```

Verify that public `/health` reports
`ai_selection.lead_assist_enabled=false`.

To disable REG.RU external generation entirely, change:

```ini
AI_MODE=template
```

and restart the relevant services. The REG.RU key can stay on disk while
disabled, although removing an unused secret is preferable.

## Production verification

The public health payload is intentionally secret-free. For a healthy enabled
deployment it should show:

```json
{
  "ai_selection": {
    "mode": "regcloud",
    "ready": true,
    "lead_assist_enabled": true
  }
}
```

Do not add API keys, base URLs containing credentials, raw customer questions,
phone numbers or other lead PII to deployment logs or health endpoints.
