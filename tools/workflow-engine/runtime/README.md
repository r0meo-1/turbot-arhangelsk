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

Known gaps deliberately remain visible:

- no `users.watch`
- no Pub/Sub notification receiver
- no persisted notification table / acknowledgement path
- polling still triggers synchronization every 30 seconds
- live Linear issue creation has an external-create → local-commit crash window

The current production VPS must keep `LINEAR_MODE=dry_run` because Linear
blocks that execution region.

## CI

The dedicated runtime workflow:

1. installs only this runtime's dependencies;
2. compiles the package;
3. runs `python -m workflow_engine.main selftest`;
4. runs runtime contract tests, including Gmail History pagination/checkpoint tests;
5. rejects obvious secret/runtime-state files.

This source tree does **not** deploy or restart the Workflow Engine merely because
it changes. Production deployment remains a separate controlled step.
