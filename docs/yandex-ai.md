# Yandex AI Studio provider (opt-in)

`AI_MODE=yandex` selects the OpenAI-compatible Yandex endpoint for destination
blurbs and the existing Telegram post-lead assistant (`/ask`, `ИИ:` and its
whitelisted quick actions). The default remains `template`. VK uses this provider
for destination blurbs; this change does not add a free-form VK chat route.
The Groq-only internal beta and its gates are unchanged.

## Configuration for a separate test environment

```ini
AI_MODE=yandex
YANDEX_AI_API_KEY=<server-only API key>
YANDEX_AI_MODEL=gpt://<folder-id>/aliceai-llm-flash/latest
AI_LEAD_ASSIST_ENABLED=false
```

The endpoint is fixed to `https://llm.api.cloud.yandex.net/v1`. Missing key,
missing/invalid folder-qualified model URI, or missing SDK means no client.
Automatic SDK retries are disabled; existing per-call timeouts and deterministic
fallbacks apply. Set `AI_LEAD_ASSIST_ENABLED=true` only in the isolated test
process when validating the post-lead assistant. Do not send test TG/VK messages
or deploy to production without the corresponding launch authorization.

Do not reuse or overwrite REGCLOUD secrets. Store the Yandex key in the server's
private environment file, never in source, chat, health output or screenshots.
The service account needs `ai.languageModels.user` and an API key with scope
`yc.ai.foundationModels.execute`. Creating credentials is a separate step.

This integration calls the model directly, not the saved AI Studio agent. Its
V1 Playground instruction is not automatically imported: the repository's shared
system prompt, input redaction, restricted-topic handling and output checks stay
in force. Lead delivery must never depend on model success. Production code
should only claim that a lead was delivered after confirmed delivery.

## Validation

```bash
python -m pytest tests/test_ai_provider.py tests/test_ai_guardrails.py tests/test_ai_chat.py
```

Tests use synthetic clients and make no model/supplier requests. Before rollout,
run a separate bounded live test with synthetic trip context and check fallback,
latency, actual token usage, handling of unverified prices and human handoff.
No API key was created and no live API smoke was performed for this code change.
Rollback: restore the previous `AI_MODE` and restart only the intended test process.

Model inference is metered separately from serverless hosting. Free-tier hosting
does not make model calls free. Do not rely on the October promotion without
account eligibility confirmation (support ticket AB012915776).

Official references:
- https://aistudio.yandex.ru/ru/docs/ai-studio/concepts/generation/models
- https://aistudio.yandex.ru/ru/docs/ai-studio/operations/get-api-key
- https://aistudio.yandex.ru/ru/docs/ai-studio/pricing
