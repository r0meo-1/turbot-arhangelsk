"""Initial canonical PostgreSQL schema; production cutover is separate."""
from pathlib import Path

from alembic import op

revision = "20261001_01"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    # This adjacent versioned SQL is immutable once the revision is released.
    op.execute(Path(__file__).with_suffix(".sql").read_text(encoding="utf-8"))


def downgrade():
    # A populated canonical store must never be discarded by a schema rollback.
    # Data rollback requires the separately verified SQLite cutover procedure.
    tables = [
        "dead_letter", "delivery", "delivery_intent", "destination_state",
        "outbox", "review_queue", "task_source", "task", "extraction_run",
        "email_event", "gmail_notification", "gmail_watch", "gmail_mailbox",
    ]
    # Serialize the emptiness check with every writer until transactional DDL
    # finishes; otherwise a concurrent insert could be dropped after the check.
    op.execute("LOCK TABLE " + ", ".join(sorted(tables)) + " IN ACCESS EXCLUSIVE MODE")
    op.execute("DO $$ BEGIN " + " ".join(
        f"IF EXISTS (SELECT 1 FROM {table}) THEN "
        "RAISE EXCEPTION 'Refusing downgrade of populated canonical state'; "
        "END IF;" for table in tables
    ) + " END $$")
    for table in tables:
        op.drop_table(table)
    op.execute("DROP FUNCTION workflow_reject_email_event_mutation()")
