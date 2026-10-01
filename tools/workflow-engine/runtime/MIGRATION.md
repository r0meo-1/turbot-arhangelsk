# Canonical state migration contract

This is the current SQLite pipeline and the required PostgreSQL cutover contract
for issue #262. PostgreSQL is not yet implemented or enabled.

## Current path

`GmailSource` paginates history from the durable `gmail_mailbox` checkpoint.
Authenticated Pub/Sub pushes persist notifications and return before extraction.
`notification_cycle` processes incremental history and advances the checkpoint
only after the batch persists. `Engine.process` runs priority rules, extraction
and validation; `Repository.ingest` starts one SQLite `BEGIN IMMEDIATE` transaction.

That transaction inserts immutable source metadata into `messages` (unique source
and external ID), applies candidates to `tasks` and `task_sources`, and inserts
versioned `TASK_CHANGED` events into `outbox`. Any failure rolls back all four.
Duplicate messages return without changing task version or outbox state.
Unresolved or conflicting candidates go to `review_queue` in the same transaction.
The current schema has no separate extraction-run or dead-letter table.

New canonical task IDs are UUIDv5 derived from the extractor's `dedupe_key` in
the fixed `workflow-engine:task:` namespace. Source replay with the same key
reconstructs the same task ID and outbox event key. Existing random IDs are
preserved on merge and must be copied verbatim during migration. IDs of outbox
rows are transport identities; the stable idempotency key is `task:<id>:v:<version>`.
Extractor changes that change the dedupe key require explicit reconciliation;
deterministic IDs alone cannot make differing extraction output equivalent.

`OutboxWorker` writes the Markdown projection and marks outbox completion.
`delivery_worker` manages `deliveries`, `destination_objects` and
`delivery_intents`; the external-create intent/reconciliation protocol must
remain intact. Production Linear delivery remains `dry_run` in the restricted
execution region.

## PostgreSQL implementation and cutover still required

- Versioned schema/Alembic for mailbox, watch, notifications, immutable email
  events, extraction runs, task/source, outbox, destination state, intents,
  reviews and explicit dead letters. Preserve current IDs, versions, source
  keys and destination mappings; do not silently discard unmatched rows.
- One transaction for canonical mutation and outbox insertion. Consumers claim
  work with PostgreSQL row locking and `SKIP LOCKED`, with recoverable leases.
- Resumable migration from a consistent SQLite snapshot, repeated imports
  idempotent, row counts/keys/versions compared before any writer switch.
- Read-only comparison against SQLite; do not independently dual-write.
- Destination crash/reconciliation and dead-letter retry acceptance on the
  PostgreSQL repository, beyond the existing SQLite checks.
- Verify resource capacity, backup and restore, migration rollback and health.
  Keep the SQLite source available for rollback. TurBot remains a separate
  service; its restart is not part of this migration.

`tests/test_task_identity_transactions.py` verifies replay identity, legacy ID
preservation, duplicate ingestion and failure after task/source writes but before
outbox insertion using SQLite. These tests do not prove PostgreSQL behavior.
