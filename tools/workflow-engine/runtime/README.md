# Workflow Engine Runtime Source

This directory is the canonical source snapshot for the standalone Workflow Engine
that was running from `/opt/workflow-engine` on 2026-09-26.

## Provenance

Sanitized source archive:

- capture: `2026-09-26T08:27:20Z`
- archive SHA-256:
  `faad64e738952ee07b5688544c16ad3232ceaf6969e692d86856c6ea52adf5ac`
- original runtime path: `/opt/workflow-engine`
- original runtime was **not** a git worktree

The file-level hashes captured on the VPS are stored in `PROVENANCE.txt`.
No `.env`, OAuth token file, SQLite database, backup file, customer email,
or secret value is part of this source snapshot.

## Current Gmail ingestion

The runtime now keeps a durable Gmail History checkpoint in SQLite:

- Gmail readonly OAuth scope
- initial bootstrap reads `users.getProfile().historyId` before listing mail
- bootstrap `messages.list` follows all result pages for the configured query
- incremental cycles use paginated `history.list(startHistoryId=...)`
- `messagesAdded` IDs are deduplicated before `messages.get(format="full")`
- the durable checkpoint advances only after the complete batch is processed
- a crash after message persistence but before checkpoint advance safely replays mail;
  `UNIQUE(source, external_id)` makes that replay idempotent
- an expired Gmail History checkpoint (HTTP 404) falls back to a paginated bootstrap
  instead of silently skipping forward
- default `POLL_INTERVAL_SECONDS=30`
- default bootstrap/recovery query `newer_than:2d`

The checkpoint is monotonic, so an older or duplicate cycle cannot move it backwards.

## Gmail watch / Pub/Sub mode

The runtime now has an opt-in authenticated push path in addition to the
existing polling fallback.

Set `GMAIL_INGEST_MODE=watch` only after external Pub/Sub infrastructure is
provisioned. Required runtime configuration:

- `GMAIL_PUBSUB_TOPIC=projects/<project>/topics/<topic>`
- `GMAIL_PUBSUB_AUDIENCE=<exact push endpoint audience>`
- `GMAIL_PUBSUB_SERVICE_ACCOUNT=<exact OIDC service-account email>`
- optional local listener controls:
  `GMAIL_PUBSUB_BIND`, `GMAIL_PUBSUB_PORT`, `GMAIL_PUBSUB_PATH`

The default listener is `127.0.0.1:8091/gmail/pubsub`. It is intentionally
not exposed directly to the internet by this source change.

Watch-mode invariants:

- Gmail `users.watch` state is persisted in `gmail_watch`;
- watch renewal is checked hourly by default and renewed before the final
  24 hours;
- every new/renewed watch enqueues a synthetic catch-up notification so a
  restart cannot leave an unseen gap between the durable checkpoint and the
  new watch;
- Pub/Sub push OIDC is verified for the configured audience and exact service
  account;
- the HTTP handler stores only Pub/Sub message ID + Gmail `historyId`, then
  returns HTTP 204;
- no message fetch, extraction, task mutation or outbox delivery occurs inside
  the HTTP request;
- duplicate Pub/Sub deliveries and duplicate `historyId` values collapse
  safely in `gmail_notification`;
- the async notification worker runs the existing History sync from the
  durable checkpoint and marks notifications done only after message
  processing and checkpoint advancement succeed.

`GMAIL_INGEST_MODE=poll` remains the default, so merging this code alone does
not expose a listener or switch production ingestion.

External provisioning still required before watch mode can be activated:

1. create/choose the Google Cloud Pub/Sub topic;
2. grant Gmail's publisher identity permission on that topic;
3. create an authenticated push subscription using the expected service
   account and audience;
4. expose the local receiver through the production HTTPS reverse proxy;
5. install the non-secret configuration values and restart only the Workflow
   Engine service;
6. verify one real notification, duplicate delivery, restart recovery and
   watch renewal without recording mail bodies or OAuth/token material.

## Linear delivery crash safety

Live Linear creation has an explicit reconciliation protocol:

1. Before any external create, persist a `delivery_intents` row and commit it.
2. Derive a deterministic marker from the canonical task ID:
   `workflow-engine-task:<task_id>`.
3. Search Linear for an existing issue whose description contains that marker.
4. Create only when no matching issue exists.
5. Store the Linear mapping, resolve the intent and mark delivery in one local commit.
6. If the process dies after Linear creates the issue but before that commit, the retry
   finds the existing marker and repairs the local mapping instead of creating a duplicate.
7. More than one matching marker fails closed for manual reconciliation.

Later task versions update the already-mapped Linear issue. Raw Linear HTTP response
bodies are not propagated into ordinary HTTP/network errors.

The current production VPS must still keep `LINEAR_MODE=dry_run` because Linear
blocks that execution region. Crash-safe code is a prerequisite for future live enablement,
not permission to bypass the regional restriction.

## CI

The dedicated runtime workflow:

1. installs only this runtime's dependencies;
2. compiles the package;
3. runs `python -m workflow_engine.main selftest`;
4. runs runtime contract tests, including Gmail History pagination/checkpoint tests;
5. tests Linear post-create crash recovery and later-version update behavior;
6. rejects obvious secret/runtime-state files.

This source tree does **not** deploy or restart the Workflow Engine merely because
it changes. Production deployment remains a separate controlled step.
