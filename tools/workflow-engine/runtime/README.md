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

## Current ingestion baseline

The imported Gmail source is a polling implementation:

- Gmail readonly OAuth scope
- `users().messages().list(..., maxResults=limit)`
- per-message `messages().get(..., format="full")`
- default `POLL_INTERVAL_SECONDS=30`
- default query `newer_than:2d`
- message dedupe through `UNIQUE(source, external_id)`

Known gaps intentionally remain visible rather than being papered over:

- no Gmail History `historyId` checkpoint
- no `history.list`
- no `users.watch`
- no Pub/Sub notification path
- no pagination of `messages.list`; a cycle is capped by `maxResults`
- live Linear issue creation has an external-create → local-commit crash window

The current production VPS must keep `LINEAR_MODE=dry_run` because Linear
blocks that execution region.

## CI

The dedicated runtime workflow:

1. installs only this runtime's dependencies;
2. compiles the package;
3. runs `python -m workflow_engine.main selftest`;
4. runs runtime contract tests;
5. rejects obvious secret/runtime-state files.

This source import does **not** deploy or restart the Workflow Engine.
