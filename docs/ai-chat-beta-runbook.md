# Closed AI chat beta runbook

Status: internal engineering beta only. This is not a legal approval and does not authorize public free-form AI chat.

## Purpose

The beta exists to exercise the guarded free-form travel assistant with a very small trusted audience before any public route exists.

Public behavior remains unchanged unless all of the following are true:

1. `AI_CHAT_ENABLED=true`;
2. the Telegram chat is allowlisted;
3. the tester explicitly sends `/ai <question>`.

`ADMIN_ID` is automatically treated as an internal tester when the master flag is enabled. Additional testers must be listed in `AI_CHAT_BETA_IDS`.

The command is intentionally absent from the public Telegram command menu.

## Configuration

```env
AI_CHAT_ENABLED=false
AI_CHAT_BETA_IDS=
AI_CHAT_MAX_CHARS=2000
AI_CHAT_TIMEOUT_SECONDS=15
GROQ_API_KEY=
GROQ_MODEL=llama-3.3-70b-versatile
```

Recommended rollout order:

1. keep `AI_CHAT_ENABLED=false` in production;
2. configure the provider credential in the server secret store, never Git;
3. enable the flag only on an internal/beta deployment;
4. test first as `ADMIN_ID`;
5. add individual numeric Telegram IDs to `AI_CHAT_BETA_IDS` only when needed;
6. inspect `/ai_stats` for fallback and handoff rates;
7. disable the flag immediately if provider/data-flow review changes or a safety regression appears.

## Data flow implemented in code

A beta tester sends free-form text to the Telegram bot. Before any external model call:

- empty input is rejected;
- payment-card, passport and secret/token patterns are blocked;
- common email and phone patterns are redacted;
- legal/contract/refund, visa/entry, health/safety and insurance topics are routed away from autonomous model answers.

Only the resulting minimized `safe_text` is eligible for the configured external model call.

After the provider returns text, deterministic output checks withhold unverified:

- prices;
- availability/inventory claims;
- visa/entry claims;
- legal/refund guarantees.

Those responses are replaced with a manager/verified-source handoff.

Redaction lowers obvious leakage risk. It does not prove that arbitrary text is anonymous.

## What this application stores

The beta telemetry table contains only aggregate counters:

- outcome key;
- count;
- last update timestamp.

It deliberately contains no Telegram chat ID, username, prompt, response text, phone, passport data or provider credential.

`/ai_stats` reads only these aggregate counters.

The normal lead database remains separate. Running `/ai` does not change the user's lead/session state.

## Provider review still required

The current code can call Groq when `GROQ_API_KEY` is configured. Before any public free-form rollout, the business owner/reviewer must verify the then-current provider terms and actual production data flow, including at minimum:

- controller/processor roles and any data-processing agreement;
- provider retention and logging behavior;
- training/use of submitted content;
- subprocessors and hosting regions;
- cross-border transfer implications for the actual user population;
- deletion/incident processes;
- whether the chosen model and API settings match the reviewed setup.

Do not infer these answers from this repository. They are external facts that can change.

## Acceptance checks for the closed beta

Record pass/fail without pasting customer text into issues or logs:

- disabled flag makes `/ai` behave as an unknown command;
- non-allowlisted chat cannot call the model;
- allowlisted safe travel question returns an identified AI-assistant answer;
- card/passport/token content never reaches the provider;
- legal/refund/visa/health/insurance questions hand off;
- invented price/availability/visa/refund claims are withheld after generation;
- provider missing, timeout, exception and empty response degrade safely;
- long input is rejected before provider use;
- `/ai_stats` contains only aggregate outcomes;
- normal `/start` lead flow is unchanged before and after beta use.

## Public release gate

Do not expose free-form AI chat to general users until:

- the provider/data-flow review above is complete for the actual deployment;
- privacy/terms text is reviewed for the actual operator and market;
- the AI safety regression suite is green;
- internal beta failures and handoff rate have been reviewed;
- GitHub issue #90 P0 business-launch gates are green.

Until then, this feature is an internal test surface, not a customer feature.
