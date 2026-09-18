# AI assistant: Russia-first compliance engineering

Status: engineering controls for review. This document is not a legal opinion and does not replace review of the actual legal entity, contracts, providers and production data flows.

## Why free-form AI chat is off by default

The existing AI feature generates a short destination blurb from limited trip parameters. A free-form chat is materially different: users can paste names, phones, passport data, payment data, health information, contract disputes and other sensitive context.

For that reason `AI_CHAT_ENABLED=false` is the default. Public free-form AI chat should not be enabled until the chosen model provider and data flow are reviewed.

## Russian-law review scope

### Personal data

Engineering must account for Federal Law 152-FZ, including:

- purpose limitation and data minimization;
- a documented lawful basis/consent where required;
- operator/processor responsibilities;
- security measures;
- Russian database/localization requirements applicable to collection of Russian citizens' personal data;
- cross-border/provider data-flow review before sending user content to a foreign model.

The code therefore redacts common identifiers and blocks obviously sensitive payment/passport/secret material before an external AI call. Redaction is a defense in depth measure, not proof that a text is anonymous.

### Tourism roles and commercial claims

Federal Law 132-FZ distinguishes the roles involved in forming and selling a tourism product. The AI must not present itself as a tour operator or create the impression that generated text is a confirmed package, booking, contract, price or availability.

Live prices and inventory must come from an enabled provider or a human manager. Generative text must not manufacture them.

### Consumer and advertising risk

The assistant must not turn a probabilistic model answer into a commercial guarantee. Material terms, refunds, contracts, advertising claims and provider attribution require deterministic business logic or a verified source.

## High-risk routing

The deterministic guardrail routes these topics away from autonomous generative answers:

- contract, claim, refund, compensation and legal disputes;
- visa and entry requirements;
- medical, vaccination and safety questions;
- insurance coverage and claims;
- payment-card, passport or secret/token material.

These questions should go to a manager and, where relevant, an official current source.

## International-grade architecture

International expansion should keep market-specific policy separate from core funnel logic:

- explicit locale, currency and timezone;
- RU/EN copy separated from business state;
- accessible keyboard/focus/labels and mobile zoom;
- provider isolation so one outage cannot break lead capture;
- attribution/affiliate tracking without client-side secrets;
- market-specific privacy/terms modules;
- observable model/provider fallback and human handoff metrics.

## Release gate

Free-form public AI chat is enabled only after:

1. provider/data-flow review;
2. PII/sensitive-data tests;
3. restricted-topic tests;
4. outage/fallback tests;
5. human handoff tests;
6. privacy/terms review for the actual operator;
7. the P0 business-launch gates in GitHub issue #90 are green.
