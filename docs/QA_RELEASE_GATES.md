# Webhook and deployment release gates

## What this change guarantees

- Telegram webhook ingress is disabled with HTTP 503 if its server secret is unset. Polling remains independent. Configured missing/wrong credentials return 403.
- VK checks the configured community before confirmation or callback handling. Confirmation still follows its separate no-secret contract. Regular callbacks reject missing/non-string/wrong secrets safely.
- Both routes reject malformed top-level/message shapes and retain their 1 MiB request limit. Authenticated unsupported provider events are ignored without business actions.
- A VK lead insert failure stops before success notifications or session deletion. Telegram persistence failures propagate to the webhook response too.
- Identified webhook deliveries receive a committed SQLite receipt before processing. Completed duplicates return success without another handler invocation, including across process restarts. Concurrent or uncertain deliveries return 503.
- VK Mini App payload handling now finishes synchronously before acknowledgement, so an untracked daemon cannot disappear after an early ACK.

## Limits that remain explicit

These are replay fences, not a transactional outbox or an exactly-once guarantee for external systems. A crash between a downstream side effect and receipt completion is ambiguous. We deliberately keep that receipt blocked for reconciliation rather than blindly repeat an external write. Normal downstream lead/CRM recovery still uses the existing durable lead records and retry logic.

Telegram receipts use the bot identity and `update_id`; VK uses community and `event_id`, with a message-ID fallback. Legacy synthetic callers without either transport identifier remain accepted for compatibility, but cannot receive the durable replay guarantee. The existing production single-worker process topology remains important for distinct events mutating the same user's in-memory session.

The ledger stores only hashed transport identity, processing state and timestamp, in each channel's existing database; the normal SQLite backup includes it. It does not store webhook bodies, tokens, names or phone numbers. Receipt hashes are internal operational metadata. There is no automatic receipt eviction: deleting successful receipts can reopen an old replay. Establish an explicit archival policy before any cleanup.

## Reconcile an ambiguous outcome

1. Inspect counts without modifying data:

   ```sh
   python deploy/webhook_receipts.py --database /opt/turbot/bot_state.sqlite
   python deploy/webhook_receipts.py --database /opt/turbot/vk_bot_state.sqlite
   ```

2. Stop the affected service before changing a receipt. A `processing` receipt may still have a live handler; do not reopen it while a worker can continue. Back up the database. Review the relevant local lead/session, provider receipt and delivery logs.
3. If the event and side effects completed, mark the exact receipt complete. If no side effect occurred and the input can safely be redelivered, allow retry. Do not infer that a timeout means the remote write failed.

   ```sh
   python deploy/webhook_receipts.py --database /opt/turbot/bot_state.sqlite \
     --receipt <64-character-receipt-hash> --decision complete --service-stopped
   # Use --decision retry only after proving redelivery cannot duplicate effects.
   ```

4. Verify provider redelivery/reconciliation availability and restart the service. The ledger cannot reconstruct an event body; provider retention limits still apply. Never mark this intervention as an automatic recovery guarantee.

## Regression and deployment pipeline

`.github/actions/regression` installs Python 3.12, SQLite CLI and the development requirements; runs the full suite; and retains a JUnit artifact for 14 days even on failure. PR checks have read-only contents permissions, a 20-minute timeout, cancellation of superseded runs and merge-queue support. Test sockets allow only loopback; external HTTP providers must be mocked. Browser E2E remains in Edge Bot CI.

All four production workflows use the same regression action before deployment and the same `turbot-production` concurrency group. Manual runs are restricted to `main`. `send-tested-bundle.sh` checks a full SHA, matching clean checkout and current remote main, then transfers those tracked files. It rejects stale queued revisions before SSH. Production secrets are loaded after tests. Telegram and VK public revisions are checked after deployment. Existing bundle restore/security probes remain enabled.

The SHA verifier currently accepts the full SHA or the existing seven-character runtime representation. Repository status-check enforcement is configured separately from workflow YAML and must be verified in GitHub. These workflow changes do not assert that branch rules were applied or that a deployment occurred.

## Evidence map and remaining release checks

| Risk | Automated evidence / remaining validation |
|---|---|
| Authentication, absent configuration and community binding | `test_webhook_security.py`: endpoint tests prove rejection before handler calls; existing bot tests cover valid confirmation. |
| Duplicate delivery and restart | `test_webhook_delivery.py`: concurrent claim, new-process replay and hard-crash fence; endpoint replay after memory reset. |
| Ambiguous remote success, timeout or HTTP 500 | Full factorial endpoint matrix and receipt exception tests assert one attempt plus 503; provider-specific reconciliation still requires a sandbox check. |
| SQLite contention and unavailable storage | Real write-lock contention and unavailable-path tests assert no processing without receipt persistence. VK failed insert preserves review. Actual disk-full/host recovery remains a staging drill. |
| Pairwise coverage | 72 cases cover all combinations of channel (2), auth (3), storage (2), downstream (3), replay (2). Full factorial coverage includes every pair. These use mocked handlers; business-flow tests separately exercise actual handlers. |
| Malformed, malicious and oversized input | New endpoint shape/size tests plus existing shared validation, HTML escaping and privacy tests. URL-fetch SSRF and proxy load tests require a separately bounded staging exercise. |
| Stale/cross-user actions | Existing review/cancel/ownership regressions remain in the full suite; no claim that every upstream identity contract is exhausted. |
| Exact deployed artifact | Sender tests reject stale, dirty and mismatched revisions before SSH; positive test inspects transmitted archive and manifest. Live revision matching is a post-merge deployment gate. |
| Failure blocks release | A nonzero pytest exit blocks downstream steps/jobs; JUnit persists. Verify required GitHub checks and controlled failing-PR behavior before calling merge enforcement proven. |
| Mobile/cross-browser and live delivery | Edge Bot CI covers synthetic browser flows. Real device, provider and manager-delivery checks require their designated test accounts/environment. No real submissions are part of this suite. |

## Local checks

```sh
python -m pytest tests/test_webhook_delivery.py tests/test_webhook_security.py tests/test_tested_bundle.py
python -m pytest --junitxml=reports/pytest.xml
python scripts/scan_repo_secrets.py
```

The deployment shell/SQLite-CLI tests run on Ubuntu CI. On Windows, report unsupported host tooling separately; do not reinterpret skipped checks as executed. JUnit records the actual candidate revision's run results.
