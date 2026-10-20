"""Explicit, offline SQLite snapshot import and read-only PostgreSQL comparison."""
import argparse
from contextlib import closing
from datetime import date, datetime, timezone
from decimal import Decimal
import json
import math
import os
from pathlib import Path
import sqlite3

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import SQLAlchemyError


class MigrationError(RuntimeError):
    """Privacy-safe migration failure, containing no source values or SQL."""


# Order follows foreign keys. Keys identify existing rows without overwriting them.
MAPPING = (
    ("gmail_mailbox", "gmail_mailbox", ("mailbox",)),
    ("gmail_watch", "gmail_watch", ("mailbox",)),
    ("gmail_notification", "gmail_notification", ("id",)),
    ("messages", "email_event", ("id",)),
    ("tasks", "task", ("id",)),
    ("task_sources", "task_source", ("source", "source_key")),
    ("review_queue", "review_queue", ("id",)),
    ("outbox", "outbox", ("id",)),
    ("destination_objects", "destination_state", ("task_id", "destination")),
    ("delivery_intents", "delivery_intent", ("task_id", "destination")),
    ("deliveries", "delivery", ("event_id", "destination")),
)
TARGETS = [target for _, target, _ in MAPPING] + ["extraction_run", "dead_letter"]
TIMESTAMPS = {"received_at", "processed_at", "updated_at", "created_at"}
JSON_FIELDS = {"payload_json", "candidate_json", "normalized_metadata"}
COLUMNS = {
    "gmail_mailbox": "mailbox history_id updated_at",
    "gmail_watch": "mailbox topic_name history_id expiration_ms updated_at",
    "gmail_notification": "id mailbox pubsub_message_id history_id status received_at processed_at",
    "messages": "id source external_id thread_id sender subject received_at processed_at",
    "tasks": "id dedupe_key title project priority_score priority owner due_date status confidence version updated_at",
    "task_sources": "task_id source external_id",
    "review_queue": "id source external_id candidate_json reason created_at resolved",
    "outbox": "id event_key event_type payload_json status attempts created_at last_error",
    "destination_objects": "task_id destination external_object_id last_synced_version updated_at",
    "delivery_intents": "task_id destination idempotency_marker status external_object_id last_synced_version last_error updated_at",
    "deliveries": "event_id destination status attempts external_object_id last_synced_version last_error updated_at",
}


def _json_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key")
        result[key] = value
    return result


def _reject_constant(value):
    raise ValueError("Non-finite JSON number")


def _json_float(value):
    parsed = float(value)
    if not math.isfinite(parsed) or Decimal(str(parsed)) != Decimal(value):
        raise ValueError("JSON number cannot be preserved exactly")
    return parsed


def normalize(field, value):
    if value is None:
        return None
    if field in TIMESTAMPS:
        parsed = value if isinstance(value, datetime) else datetime.fromisoformat(value)
        # SQLite delivery defaults use CURRENT_TIMESTAMP, which is UTC.
        return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)
    if field == "due_date":
        return value if isinstance(value, date) else date.fromisoformat(value)
    if field == "history_id":
        raw = str(value)
        if not raw.isascii() or not raw.isdigit() or len(raw.lstrip("0") or "0") > 20:
            raise ValueError("Invalid history ID")
        return Decimal(raw)
    if field == "resolved":
        if value not in (0, 1, False, True):
            raise ValueError("Invalid boolean")
        return bool(value)
    if field in JSON_FIELDS:
        parsed = json.loads(value, parse_constant=_reject_constant, parse_float=_json_float, object_pairs_hook=_json_object) if isinstance(value, str) else value
        if not isinstance(parsed, dict):
            raise ValueError("Expected JSON object")
        # Reject overflow-to-infinity too (e.g. 1e999), recursively.
        json.dumps(parsed, allow_nan=False)
        return parsed
    if field == "confidence" and not math.isfinite(value):
        raise ValueError("Invalid confidence")
    return value


def read_snapshot(path):
    """Read a consistent, read-only view; never checkpoint or modify SQLite."""
    try:
        with closing(sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)) as source:
            source.row_factory = sqlite3.Row
            source.execute("BEGIN")
            if source.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise MigrationError("SQLite integrity check failed")
            names = {r[0] for r in source.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            expected = {table for table, _, _ in MAPPING}
            if names - expected - {"sqlite_sequence"} or expected - names:
                raise MigrationError("SQLite snapshot table set does not match the supported runtime schema")
            for table in expected:
                columns = {r[1] for r in source.execute(f"PRAGMA table_info({table})")}
                if columns != set(COLUMNS[table].split()):
                    raise MigrationError(f"Unsupported SQLite columns in {table}")
            tasks = {r["id"]: r["dedupe_key"] for r in source.execute("SELECT id,dedupe_key FROM tasks")}
            result = {}
            for table, target, _ in MAPPING:
                rows = []
                for raw in source.execute(f"SELECT * FROM {table}"):
                    row = dict(raw)
                    if table == "task_sources":
                        if row["task_id"] not in tasks:
                            raise MigrationError("task_sources contains an orphan task reference")
                        row["source_key"] = json.dumps([row["external_id"], tasks[row["task_id"]]], ensure_ascii=False, separators=(",", ":"))
                    if table == "messages":
                        row["normalized_metadata"] = {}
                    for field, value in row.items():
                        try:
                            row[field] = normalize(field, value)
                        except (ValueError, TypeError, OverflowError):
                            raise MigrationError(f"Invalid source field: {table}.{field}") from None
                    rows.append(row)
                result[target] = rows
            return result
    except (sqlite3.Error, OSError, KeyError):
        raise MigrationError("Cannot read supported SQLite snapshot") from None


def _bind(field):
    return f"CAST(:{field} AS JSONB)" if field in JSON_FIELDS else f":{field}"


def _params(row):
    return {key: json.dumps(value, ensure_ascii=False, allow_nan=False) if key in JSON_FIELDS else value for key, value in row.items()}


def _equal(expected, actual):
    for key, value in expected.items():
        observed = normalize(key, actual[key])
        if key in JSON_FIELDS:
            # Python treats True == 1; JSON boolean and number are different data.
            if json.dumps(observed, sort_keys=True) != json.dumps(value, sort_keys=True):
                return False
        elif observed != value:
            return False
    return True


def transfer(engine, snapshot, *, apply=False):
    """Import atomically or compare without writes; report counts only."""
    try:
        with engine.begin() as target:
            if not apply:
                target.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"))
            if target.scalar(text("SELECT version_num FROM alembic_version")) != "20261001_01":
                raise MigrationError("Unsupported PostgreSQL migration revision")
            if apply:
                # Offline import only. Serialize against every canonical writer.
                target.execute(text("LOCK TABLE " + ", ".join(sorted(TARGETS)) + " IN ACCESS EXCLUSIVE MODE"))
            counts = {}
            for _, table, keys in MAPPING:
                rows = snapshot[table]
                for row in rows:
                    if apply:
                        fields = list(row)
                        statement = f"INSERT INTO {table} ({','.join(fields)}) VALUES ({','.join(_bind(field) for field in fields)}) ON CONFLICT DO NOTHING"
                        target.execute(text(statement), _params(row))
                    where = " AND ".join(f"{key}=:{key}" for key in keys)
                    actual = target.execute(text(f"SELECT * FROM {table} WHERE {where}"), {key: row[key] for key in keys}).mappings().first()
                    if actual is None or not _equal(row, actual):
                        raise MigrationError(f"Source/target row mismatch in {table}")
                    if table == "outbox" and (actual["lease_owner"] is not None or actual["lease_until"] is not None):
                        raise MigrationError("Target outbox has active lease state")
                count = target.scalar(text(f"SELECT count(*) FROM {table}"))
                if count != len(rows):
                    raise MigrationError(f"Source/target count mismatch in {table}")
                counts[table] = count
            for table in ("extraction_run", "dead_letter"):
                if target.scalar(text(f"SELECT count(*) FROM {table}")):
                    raise MigrationError(f"Target has unmatched runtime state in {table}")
            if apply:
                # Explicit legacy IDs must not collide with later identity inserts.
                for table in ("email_event", "gmail_notification"):
                    next_id = target.scalar(text(f"SELECT COALESCE(MAX(id),0) + 1 FROM {table}"))
                    # RESTART is transactional; setval would survive rollback.
                    sequence = target.scalar(text(f"SELECT pg_get_serial_sequence('{table}','id')"))
                    target.execute(text(f"ALTER SEQUENCE {sequence} RESTART WITH {int(next_id)}"))
            return {"mode": "import" if apply else "compare", "verified_counts": counts}
    except SQLAlchemyError:
        # Driver exceptions can include private row values even with hidden params.
        raise MigrationError("PostgreSQL import/comparison failed; transaction rolled back") from None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sqlite-snapshot", required=True)
    parser.add_argument("--mode", choices=("validate", "compare", "import"), default="compare")
    args = parser.parse_args()
    engine = None
    try:
        snapshot = read_snapshot(args.sqlite_snapshot)
        if args.mode == "validate":
            report = {"mode": "validate", "source_counts": {table: len(rows) for table, rows in snapshot.items()}}
        else:
            url = os.environ.get("WORKFLOW_POSTGRES_MIGRATION_URL", "")
            if not url or make_url(url).get_backend_name() != "postgresql":
                raise MigrationError("A PostgreSQL migration URL is required in the environment")
            engine = create_engine(url, hide_parameters=True)
            report = transfer(engine, snapshot, apply=args.mode == "import")
        print(json.dumps(report, sort_keys=True))
    except (MigrationError, ValueError, SQLAlchemyError):
        parser.exit(1, "Migration failed; no source values or credentials are emitted. Validate schema, source data and target conflicts.\n")
    finally:
        if engine is not None:
            engine.dispose()


if __name__ == "__main__":
    main()
