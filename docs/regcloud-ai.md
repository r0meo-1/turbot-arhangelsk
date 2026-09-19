# REG.RU Cloud AI provider

TurBot can use REG.RU Cloud's OpenAI-compatible API for the short destination
blurb that is generated after a lead is durably saved.

This integration is deliberately limited to the structured blurb flow. Public
free-form AI chat remains behind its existing Groq-specific beta/ZDR gates until
the REG.RU data flow and operator/privacy requirements are reviewed separately.

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
```

Do not infer the base URL or model ID from the human-readable model label in the
Sandbox. The code intentionally requires all three values and refuses non-HTTPS
base URLs.

Keep the file private:

```bash
sudo chown turbot:turbot /opt/turbot/.env
sudo chmod 600 /opt/turbot/.env
```

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

## Rollback

Change:

```ini
AI_MODE=template
```

then restart the two services. The REG.RU key can stay on disk while disabled,
although removing an unused secret is preferable.
