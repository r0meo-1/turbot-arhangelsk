# AI safety and Russian-law engineering baseline

This document is an engineering control checklist, not a legal opinion or a claim of legal compliance.

## Scope

TurBot's current AI feature generates a short destination blurb. It is not allowed to create or confirm a booking, set contract terms, decide a refund claim, provide binding visa advice, or make automated legal decisions about a customer.

The implementation uses a local template by default. Any external AI provider must be explicitly enabled and reviewed before production use.

## Russian-law areas considered

The engineering controls are designed around risks relevant to:

- Federal Law No. 152-FZ "On Personal Data";
- Law of the Russian Federation No. 2300-1 "On Protection of Consumer Rights";
- Federal Law No. 132-FZ "On the Fundamentals of Tourist Activity in the Russian Federation";
- Federal Law No. 38-FZ "On Advertising".

These laws change. Legal review must use the current effective text at the time of launch and after material product changes.

## Product rules

1. Minimise data sent to AI. The AI destination-blurb call receives only destination, dates, party size and budget. It must not receive name, phone, passport, payment card, health, biometric or manager/CRM identifiers.
2. Treat customer-entered values as untrusted data, not prompt instructions.
3. Never fabricate prices, availability, hotel names, flight inventory, discounts, booking state or guarantees.
4. AI output is preliminary informational content, not an offer, contract, booking confirmation or final commercial condition.
5. Contract, refund, complaint, visa, insurance and consumer-rights questions must not receive categorical legal conclusions from the model. Current documents and official sources take priority, with manager/human review where needed.
6. External AI must fail closed to the local template on timeout, provider failure, empty output or unsupported mode.
7. Do not scale paid traffic with an external AI provider until data-flow, provider terms, cross-border/data-processing questions and production logging have been reviewed.
8. Do not put secrets or unnecessary personal data into prompts, logs, screenshots or QA issues.

## International-quality baseline

For a business launch, AI is also expected to have:

- prompt-injection regression tests;
- deterministic fallbacks;
- explicit timeouts;
- provider feature flags;
- no hidden claims that generated text is live inventory;
- human confirmation of commercial terms;
- observability for provider errors without logging customer secrets;
- a rollback path to template mode.

## Release gate

AI can be considered business-launch ready only when tests are green, the production mode is known, the external-provider data flow has been reviewed if enabled, and the rest of the business-readiness checklist in issue #90 is satisfied.
