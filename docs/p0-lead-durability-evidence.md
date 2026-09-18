# P0 launch evidence: lead durability

Status: automated evidence for GitHub issue #90. This document records which launch-gate claims are backed by repeatable tests. It is not a substitute for the final production smoke.

## Telegram lead path

| P0 requirement | Automated evidence | Status |
| --- | --- | --- |
| Telegram `/start → consent → destination → departure → dates → tourists → budget → contact → review → save` | `test_p0_telegram_full_funnel_reaches_one_durable_lead` | Verified in CI |
| Contact creates a review draft, not a lead before explicit send | `test_contact_only_creates_draft_until_confirmed` | Verified in CI |
| Repeated review send / old callback does not duplicate a lead | `test_contact_only_creates_draft_until_confirmed`, `test_review_edit_invalidates_old_send_button` | Verified in CI |
| Concurrent completion / double-tap creates one lead | `test_concurrent_completion_creates_one_lead` | Verified in CI |
| Local SQLite lead exists before downstream CRM/network actions | `test_p0_local_lead_insert_precedes_downstream_actions` | Verified in CI |
| Failed local INSERT keeps review available for retry and does not notify manager | `test_failed_lead_save_preserves_review_for_retry` | Verified in CI |
| Manager notification exception cannot destroy or strand an already-saved lead | `test_p0_manager_notification_exception_does_not_strand_saved_lead` | Verified in CI |
| MDT outage keeps local lead pending and later retries with stable delivery key | `test_p0_mdt_outage_keeps_local_lead_pending_then_recovers`, `test_telegram_mdt_retry_persists_and_recovers` | Verified in CI |

## Telegram Mini App

| P0 requirement | Automated evidence | Status |
| --- | --- | --- |
| Browser form reaches review, posts versioned payload, and closes | `tests/test_telegram_miniapp_e2e.py::test_telegram_miniapp_browser_reviews_then_posts_v2_payload_and_closes` | Edge Bot CI |
| Signed server submission continues at bot contact step | `tests/test_miniapp_route.py::test_menu_miniapp_submission_creates_contact_step` | Verified in CI |
| Child ages, nights, budget scope and direct-flight preference survive session persistence and final lead save | `test_p0_miniapp_preferences_survive_session_reload_and_lead_save` | Verified in CI |
| Mini App details survive review and manager handoff | `test_miniapp_details_survive_review_and_manager_handoff` | Verified in CI |

The persistence test exists because review text alone is weak evidence. Before this gate was added, `budget_scope`, `direct_only` and `nights` existed in memory but were absent from the SQLite session/lead schema. The P0 work adds additive SQLite migrations and persists those fields.

## VK Mini App

The existing Edge Bot browser test
`tests/test_vk_miniapp_e2e.py::test_vk_miniapp_browser_roundtrip_sends_review_payload_with_clipboard_fallback`
covers signed VK launch parameters, form review, `POST /vk/miniapp/draft`, normalized draft persistence and return/continuation to the VK community chat.

Backend Mini App validation is additionally covered by `tests/test_vk_miniapp.py`.

## P0 items not claimed complete here

This change does **not** claim the following #90 gates are complete unless separately evidenced:

- destination autocomplete coverage for countries, resorts and free text;
- departure autocomplete coverage for common cities and free text;
- final production privacy/external-link review;
- Booking.com / Tripadvisor production-account readiness;
- final production smoke after the last deploy;
- any real booking or payment flow.

Automated tests must not create a real booking or payment. Provider calls in unit acceptance paths are disabled, injected or mocked.

## Release discipline

A checkbox in #90 should only be marked complete when:

1. a deterministic automated test or explicit production check exists;
2. the relevant CI is green on the merged revision;
3. the evidence still describes the current code and deployment.

That sounds painfully obvious, which is why software teams have repeatedly invented ceremonies to avoid doing exactly this.
