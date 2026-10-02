# PostgreSQL schema stage

This directory defines the initial canonical-state schema for #262. It is not
wired into production ingestion, adapters or deployment. The SQLite repository
remains the only configured writer. Dependencies are intentionally separate from
the runtime requirements.

Run against a separately provisioned PostgreSQL database:

```sh
python -m pip install -r tools/workflow-engine/runtime/postgres/requirements.txt
# Set WORKFLOW_POSTGRES_MIGRATION_URL through the environment, never in Git.
python -m alembic -c tools/workflow-engine/runtime/postgres/alembic.ini upgrade head
```

The Alembic URL must use a PostgreSQL driver (e.g. `postgresql+psycopg`). A URL
is never stored in the INI or printed by the migration environment. To review
DDL without a server, use `upgrade head --sql` with a synthetic PostgreSQL URL.
The revision's adjacent SQL file is part of the immutable migration history;
later schema changes require a new revision.

The integration tests truncate their explicit test tables and therefore require
a loopback database name ending in `_test`. CI provides an ephemeral PostgreSQL
17 service. Upgrade twice, empty downgrade/re-upgrade, populated-downgrade
rejection, immutable email events, uniqueness, exact large history IDs,
transaction rollback, and real `SKIP LOCKED` concurrency are tested.
Downgrade locks all canonical tables before checking emptiness, waits for
in-flight writers and refuses populated state. The concurrent-writer test
verifies committed data and revision survive the refusal. Data rollback must use the later verified
SQLite migration/cutover procedure; it is not dropping a populated schema.

## Offline snapshot import and comparison

`import_sqlite.py` accepts a consistent SQLite backup made with the SQLite backup
API (not a copy of a live database file without its WAL). It opens the snapshot
read-only and checks the supported table/column set and value conversions.
Use synthetic data in tests; never commit a production snapshot or database URL.

```sh
python tools/workflow-engine/runtime/postgres/import_sqlite.py --sqlite-snapshot /secure/workflow-snapshot.db --mode validate
python tools/workflow-engine/runtime/postgres/import_sqlite.py --sqlite-snapshot /secure/workflow-snapshot.db --mode import
python tools/workflow-engine/runtime/postgres/import_sqlite.py --sqlite-snapshot /secure/workflow-snapshot.db --mode compare
```

`validate` checks source schema and conversions only; PostgreSQL constraints are
verified during import. `compare` is the default and uses a read-only repeatable
read transaction. `import` requires an already upgraded, offline target via
`WORKFLOW_POSTGRES_MIGRATION_URL`. It locks canonical tables, inserts missing
rows, verifies every mapped field and total count, and commits everything in one
transaction. A matching partial import can resume. Repeated import is idempotent;
conflicting/extra rows, active leases and unmatched new processing history abort
the entire transaction. Existing rows are never overwritten. Legacy identity
sequences restart transactionally beyond imported IDs. Reports contain counts
only; errors suppress source values and credentials.

Stop target workers before import. The importer is not an online synchronization
tool: continued SQLite writes require a new final snapshot and a separate approved
cutover plan. Preserve SQLite for rollback. No runtime writer or deployment is
switched by these commands.

## Preservation mapping

| SQLite source | PostgreSQL target | Required preservation |
| --- | --- | --- |
| gmail_mailbox/watch/notification | same names | Mailbox, history IDs, receipt/processing state, watch expiry |
| messages | email_event | Existing IDs and source/external IDs; sender/subject metadata and times |
| tasks | task | Existing task IDs, dedupe keys, versions, priority, owner, due date and state |
| task_sources | task_source | Source/external ID and task ID; source_key derived unambiguously from external ID plus canonical dedupe key |
| review_queue | review_queue | Candidate JSON, reason and resolution state |
| outbox | outbox | Existing row IDs, event keys, payloads, attempt/status/error state |
| destination_objects | destination_state | Task/destination IDs, external object and last synced version |
| delivery_intents | delivery_intent | Existing marker, external object, version, status and error |
| deliveries | delivery | Existing event/destination history and statuses |

`extraction_run` and `dead_letter` represent future observable processing and
retry state, not fabricated history for old rows. Timestamp/date/JSON/boolean
conversion must validate inputs and fail on incompatible rows. In particular,
new status/foreign-key/uniqueness constraints must not cause silent filtering
of legacy state. Synthetic integration tests cover consistent snapshot import,
row/key/version comparison, resumability, conflict refusal and full rollback.
Complete service integration, external adapter crash recovery/reconciliation,
destination-specific DLQ handling and production snapshot/cutover acceptance
remain to implement and verify. The persistence boundaries below are isolated
from production service selection.

## Canonical ingestion boundary

`canonical.CanonicalStore(engine).ingest(message, candidates, validator)` provides
the synchronous PostgreSQL transaction for the existing runtime models and
validator. The caller supplies and disposes a PostgreSQL SQLAlchemy engine using
READ COMMITTED isolation. Run with `runtime` on `PYTHONPATH`. The service still
constructs the SQLite repository; no environment switch enables this new class.

One transaction records the immutable email, validates candidates, updates tasks,
links sources, and inserts versioned outbox events. A repeated source/external ID
returns false without changing canonical state. The whole transaction rolls back
if any candidate or database write fails. Transaction-scoped advisory locks cover
new and existing dedupe keys; locks are ordered to support concurrent multi-task
ingestion. Existing task rows are also locked before validation and merging.
Duplicate candidate keys within one extraction are rejected before writing.

New IDs use the SQLite runtime's deterministic UUID rule; imported IDs remain
unchanged. Date values are converted to the validator's ISO-string contract,
preserving compatible deadline merges and conflict review. This boundary does
not store email bodies or fabricate extraction history. Database failures expose
a generic error rather than driver SQL or source values; callers should not log
private arguments or chained exception internals.

Real PostgreSQL tests cover concurrent duplicate and distinct emails, reversed
candidate order, task/version/source/event consistency, review decisions, a late
outbox failure rolling back both new and existing state, and ingestion after
legacy import. The asynchronous service adapter, delivery-worker integration and
external destination reconciliation are not yet implemented. Those must be
complete before production cutover. Queue leases/retries are described below.

## Gmail durable state boundary

`gmail.GmailStore(engine)` provides synchronous counterparts of the seven SQLite
Gmail state methods. History IDs use exact NUMERIC values (up to the schema's 20
digits) and return canonical decimal strings. Concurrent checkpoint upserts only
advance; stale and repeated checkpoints leave both the value and update time
unchanged. This store does not own or dispose the caller's engine.

Notifications deduplicate by Pub/Sub message ID and mailbox/history ID. Reads
select the numerically highest pending history. Acknowledgement changes only
pending notifications at or below the requested history within one mailbox;
later notifications remain pending. Receiving or acknowledging a notification
does not advance the ingestion checkpoint or create an email/task. The caller
must acknowledge only after successfully ingesting the corresponding history.

Watch upserts retain the response with the greatest expiration time, preventing
an older concurrent response from replacing a later renewal. Equal expirations
are no-ops, including differing topic/history values; this API is for renewal,
not forcing subscription reconfiguration. Watch history is independent of the
ingestion checkpoint. Tests cover precision, new store instances, concurrent
writes, replay, bounded acknowledgement, invalid inputs and rollback.

The service and authenticated Pub/Sub receiver still use SQLite. A future async
adapter must compose these methods with canonical ingestion and delivery before
any writer cutover; passing the synchronous store to the existing async service
is not supported. These tests do not prove a live Gmail subscription or delivery.

## Leased outbox boundary

`outbox.OutboxStore(engine)` implements synchronous `claim`, `renew`, `complete`,
`fail` and explicit `retry_dead` operations. Claim uses `FOR UPDATE SKIP LOCKED`
to take one pending or expired event without waiting for other workers' rows.
Each claim increments the attempt count and returns a fresh `lease_owner` UUID
token. Pass that exact token with the event ID when renewing, completing or
failing. A token identifies one attempt, not a reusable worker ID.

Leases use the database clock and last 1–3600 seconds (default 60). Renewal never
shortens an existing lease. Expired tokens cannot update state even before a
replacement claim, and a reclaimed event cannot be acknowledged by its previous
token. Mutations lock the event before checking wall-clock expiry, so a lock wait
does not grant extra time based on an old transaction timestamp.

The eighth failed attempt moves the event to `dead` and records a dead-letter
summary atomically. Crashes count as attempts too: claim sweeps up to 100
exhausted pending/expired events into dead letters before taking runnable work.
An unexpired final attempt is left alone. Failure reasons are allowlisted codes;
raw exception/source text is rejected. Queue failures use destination
`canonical-outbox`, separate from external destination delivery failures.

`retry_dead` explicitly resets a dead event's attempt budget and marks its queue
dead-letter summary retried in one transaction. It requires the corresponding
unretried queue failure and does not reset pending/processing/done events. A later
dead-letter cycle updates the same summary and clears the retry marker; this is
the latest failure summary, not a complete immutable retry audit log.

These operations provide recoverable queue ownership, not exactly-once external
delivery or per-task version ordering. A worker may crash after external success;
destination idempotency and reconciliation remain required. The existing async
worker is not wired to this store. Its adapter must renew leases during long work,
handle rejected stale acknowledgements, provide retry pacing, and preserve
destination idempotency before production cutover.
