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

The endpoint is fixed to `https://ai.api.cloud.yandex.net/v1`, with the model's
folder ID passed as OpenAI `project`, following the current official SDK example.
OpenAI >=1.55.3 is required (1.50.0 fails to construct with HTTPX 0.28).
Missing key,
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
python -m pytest tests/test_ai_provider.py tests/test_ai_guardrails.py tests/test_ai_chat.py tests/test_yandex_ai_sdk.py tests/test_yandex_smoke.py
```

Tests use synthetic clients and make no model/supplier requests. Before rollout,
run a separate bounded live test with synthetic trip context and check fallback,
latency, actual token usage, handling of unverified prices and human handoff.
No API key was created and no live API smoke was performed for this code change.
Rollback: restore the previous `AI_MODE` and restart only the intended test process.

## Isolated server check (no bot processes)

Use a separate checkout and venv owned by `turbot`, outside `/opt/turbot`.
Do not source the production `.env`, import `bot.py`/`vk_bot.py`, run Flask,
start polling, configure webhooks, or restart either production service.
The smoke script imports only the shared AI modules. It has no messaging,
lead, booking, payment or supplier tools and does not open a database.

```bash
# From the isolated checkout, using its own virtual environment:
.venv/bin/python tools/yandex_ai_smoke.py
# After securely placing the EXISTING key in the sandbox's private .env:
.venv/bin/python tools/yandex_ai_smoke.py --env-file ../.env --live > ../smoke.json
```

The config probe prints presence only. The live command makes at most three
sequential model calls (450 output tokens each, 10s per-call SDK timeout, zero
retries), then tests manager routing without a model call. It stops after the
first SDK error. HTTPX timeouts apply to individual network operations, not a
strict overall wall-clock deadline. Reports contain model, latency, token usage,
reason codes and exception class; never keys, raw errors or model text.
Exit 2 means missing/invalid config, exit 1 means smoke failed, exit 0 means
transport smoke completed. Transport success does not establish answer quality.

Do not create a duplicate key. The operator can use `read -rs` in their existing
SSH session to put the existing key in a mode-600 sandbox file, without shell
history or chat exposure. Never copy REGCLOUD or bot credentials into it.
For rollback, stop only the sandbox process; no production configuration needs
restoring because it was not changed. Remove the sandbox key after testing.

## Scope and remaining launch gates

- Telegram `/ask`, `ИИ:` and quick actions use the current in-memory or recent
  saved trip, not a conversation history. Saved child ages are included.
  Corrections in the current question take prompt priority, but are NOT written
  back to the lead and are not remembered in the next independent `/ask` call.
- Explicit manager requests bypass the model and set `handoff_required`.
  The existing Telegram caller invokes the manager notification path. The shared
  reply does not claim delivery. Actual notification delivery is outside these
  AI-only tests; a flag or attempted notification is not a delivery receipt.
- Both destination blurbs (Telegram/VK) and Telegram chat check generated text
  for unverified commercial facts and action language. Rejected blurbs use the
  deterministic template; rejected chat replies require a manager. Exceptions
  are logged by class only to avoid leaking provider error bodies.
- Checks are conservative lexical safeguards, not a semantic guarantee against
  every paraphrase, language or injection. Benign action mentions and budget
  echoes can also be rejected. More natural multi-turn chat and a free-form VK
  route require separate work and acceptance testing before public rollout.
- Before production: bounded live API test with the existing key, reviewed
  synthetic answer-quality evaluation, isolated bot routing/delivery tests with
  all outbound clients replaced, explicit traffic and deployment authorization.

Model inference is metered separately from serverless hosting. Free-tier hosting
does not make model calls free. Do not rely on the October promotion without
account eligibility confirmation (support ticket AB012915776).

Official references:
- https://aistudio.yandex.ru/ru/docs/ai-studio/concepts/generation/models
- https://aistudio.yandex.ru/ru/docs/ai-studio/operations/get-api-key
- https://aistudio.yandex.ru/ru/docs/ai-studio/pricing
- https://aistudio.yandex.ru/ru/docs/ai-studio/operations/generation/completions-basic
