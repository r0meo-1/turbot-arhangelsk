# TurBot analytics event contract

This document describes the production analytics events used for acquisition,
lead delivery and partner handoff. The goal is operational and campaign
measurement without storing customer identity in analytics tables.

## Privacy rule

Analytics events must not contain:

- customer name or phone;
- Telegram/VK user or chat ID;
- username or profile URL;
- signed Telegram/VK launch payload;
- free-form customer notes/messages;
- API keys, access tokens or passwords;
- full affiliate/provider URLs.

Campaign/source tags are bounded and sanitized. A source containing an email-like
value, URL or a long numeric identifier is replaced with `redacted`.

## Acquisition funnel

Table: `acquisition_funnel_events`.

Stored fields:

| Field | Meaning |
| --- | --- |
| `channel` | bounded channel key such as `telegram`, `vk`, `website` |
| `source` | bounded first-touch/campaign source or `direct` |
| `stage` | bounded funnel stage |
| `outcome` | bounded coarse result |
| `created_at` | Unix timestamp |

No customer identifier is accepted by the funnel recorder.

### Event mapping

| Product milestone | Server analytics representation |
| --- | --- |
| Funnel start | Telegram/VK: `stage=start, outcome=opened`. Website form starts are measured separately by the site analytics layer rather than creating a TurBot identity-bearing request. |
| Review screen | **No separate persistent server event.** Review is a local Mini App state before save. Browser E2E tests verify that review is reached and preserves entered data. This avoids an extra tracking request merely for opening review. |
| Durable save / lead accepted | `stage=lead, outcome=accepted`. Duplicate/idempotent paths may use `outcome=duplicate`; save failures are additionally represented by privacy-safe operational counters. |
| Manager handoff | `stage=manager, outcome=delivered` or `failed`. |
| AI → human escalation | `stage=ai_handoff, outcome=escalated`. |

The health/admin aggregate uses a bounded recent window and reports grouped
counts, not raw event rows or customer records.

Default funnel-event retention is controlled by
`FUNNEL_ANALYTICS_RETENTION_DAYS` and is currently **365 days**. Setting it to
`0` disables cleanup and is not the recommended production posture.

## Partner click analytics

Table: `partner_clicks`.

Stored fields:

- `service`: bounded service class, currently `hotel`, `esim` or `transfer`;
- `destination`: bounded destination text used for aggregate product analysis;
- `mode`: `api`, `redirect` or `direct`;
- `source`: bounded surface/source key;
- `created_at`: Unix timestamp.

The table deliberately has no `chat_id`, `user_id`, username, phone, signed
platform payload, credential or affiliate URL.

Default retention is controlled by
`PARTNER_ANALYTICS_RETENTION_DAYS` and is currently **365 days**.

## Operational lead-delivery metrics

Separate privacy-safe operational metrics cover durable lead acceptance,
save failures, duplicates/rejects where applicable, provider outcomes and
manager-delivery latency. They expose aggregate counts/latency snapshots only.

These operational metrics complement the acquisition funnel; they are not a
second copy of customer lead data.

## Acceptance / interpretation

A campaign funnel can therefore be interpreted as:

`start → local review → durable lead save → manager delivery → optional partner click`

The review milestone is intentionally verified by UI/browser tests rather than
persisted as a standalone server analytics row. Any future change that adds a
review telemetry request must preserve the same minimization rule and must not
introduce platform/customer identifiers.
