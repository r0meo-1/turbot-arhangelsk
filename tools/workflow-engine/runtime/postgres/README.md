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
Downgrade refuses populated state. Data rollback must use the later verified
SQLite migration/cutover procedure; it is not dropping a populated schema.

## Preservation mapping for the future importer

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
of legacy state. Consistent snapshot import, row/key/version comparison,
resumability, PostgreSQL repository/lease worker, adapter crash recovery, DLQ
retry and production cutover remain to implement and verify.

Schema tests prove PostgreSQL constraints and transaction/locking primitives;
they do not prove the production repository or delivery-worker implementation.
