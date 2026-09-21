# AI lead-assist production gate

This runbook controls the customer post-lead/free-form AI assist switch. It is deliberately separate from deterministic funnel behavior and structured destination-blurb/provider-readiness observability.

## Default deploy behavior

- `.github/workflows/deploy-bundle.yml` reads the GitHub **production environment variable** `AI_LEAD_ASSIST_ENABLED`.
- If the variable is absent, empty, or not configured, the workflow defaults the desired state to `false`.
- The workflow sends only the boolean desired state to the restricted production deployer.
- After applying the state, `/health` must report the same `ai_selection.lead_assist_enabled` value.
- Provider readiness (`ai_selection.ready`, mode and model name) remains observable even when lead assist is disabled.

A normal deploy must never enable lead assist merely because an external provider is technically ready.

## Preconditions before enabling

Do not set `AI_LEAD_ASSIST_ENABLED=true` until the release owner has recorded evidence that the applicable production gates are satisfied, including:

1. operator / Roskomnadzor status and required personal-data documentation are resolved (#128);
2. external AI processor contract, hosting/data location, subprocessors and retention are confirmed (#132);
3. customer-facing privacy/legal disclosures match the enabled REG.RU lead-assist data flow (#205);
4. the current release checklist has no unresolved blocker that explicitly forbids external/free-form AI (#90/#92);
5. regression tests and production health checks pass without exposing customer prompts, identifiers, tokens or provider credentials.

These checks are release controls, not a statement that every legal obligation is automatically satisfied by the software.

## Enable procedure

1. In the GitHub `production` environment, set environment variable `AI_LEAD_ASSIST_ENABLED` to `true`.
2. Run/allow the normal `Deploy TurBot` -> `Deploy TurBot Bundle` chain.
3. Confirm the bundle job prints a non-secret state line showing `enabled=yes` and provider readiness.
4. Confirm `https://bot.r0meo1.ru/health` reports `ai_selection.lead_assist_enabled=true`.
5. Record only redacted PASS metadata in the release/incident evidence. Never paste customer questions or credentials.

## Disable / rollback procedure

1. Set the GitHub `production` environment variable `AI_LEAD_ASSIST_ENABLED` to `false`, or remove it entirely.
2. Run `Deploy TurBot Bundle`.
3. Confirm `/health` reports `ai_selection.lead_assist_enabled=false`.

Removing the variable is intentionally fail-closed because the workflow default is `false`.

## Regression contract

`tests/test_ai_lead_assist_deploy.py` must continue to prove that:

- the deploy bundle installs the restricted deployer before applying the lead-assist config marker;
- the workflow does not hard-code `AI_LEAD_ASSIST_ENABLED: "true"`;
- the default expression falls back to `false`;
- production health is checked against the desired state;
- provider readiness remains observable while enablement is independently controlled.
