"""Synthetic offline import, restart, conflict and rollback acceptance."""
import ast
from contextlib import closing
import importlib.util
import sqlite3

import pytest
from sqlalchemy import text

from conftest import ROOT, TABLES

spec = importlib.util.spec_from_file_location("import_sqlite", ROOT / "import_sqlite.py")
migration = importlib.util.module_from_spec(spec)
spec.loader.exec_module(migration)


@pytest.fixture
def snapshot_path(tmp_path):
    path = tmp_path / "synthetic.db"
    with closing(sqlite3.connect(path)) as db:
        for name in ("db.py", "delivery_worker.py"):
            tree = ast.parse((ROOT.parent / "workflow_engine" / name).read_text(encoding="utf-8"))
            schema = next(ast.literal_eval(node.value) for node in tree.body
                          if isinstance(node, ast.Assign)
                          and any(isinstance(t, ast.Name) and t.id == "SCHEMA" for t in node.targets))
            db.executescript(schema)
        db.executescript("""
            INSERT INTO gmail_mailbox VALUES ('me','18446744073709551615','2026-10-01T10:00:00+03:00');
            INSERT INTO gmail_watch VALUES ('me','synthetic-topic','18446744073709551615',1800000000000,'2026-10-01T07:00:00Z');
            INSERT INTO gmail_notification VALUES (23,'me','synthetic-push','18446744073709551615','done','2026-10-01T07:00:00Z','2026-10-01T07:01:00Z');
            INSERT INTO messages VALUES (41,'gmail','synthetic-email','synthetic-thread','qa@example.invalid','Synthetic','2026-10-01T07:00:00Z','2026-10-01T07:01:00Z');
            INSERT INTO tasks VALUES ('legacy-task','synthetic-key','Synthetic task','QA',50,'P2',NULL,'2026-10-05','open',0.85,3,'2026-10-01T07:01:00Z');
            INSERT INTO task_sources VALUES ('legacy-task','gmail','synthetic-email');
            INSERT INTO review_queue VALUES ('review','gmail','synthetic-email','{"title":"Synthetic"}','review', '2026-10-01T07:01:00Z',0);
            INSERT INTO outbox VALUES ('event','task:legacy-task:v:3','TASK_CHANGED','{"task_id":"legacy-task","version":3,"score":0.85}','done',2,'2026-10-01T07:01:00Z','synthetic retry');
            INSERT INTO destination_objects VALUES ('legacy-task','linear','synthetic-object',3,'2026-10-01 07:01:00');
            INSERT INTO delivery_intents VALUES ('legacy-task','linear','synthetic-marker','done','synthetic-object',3,NULL,'2026-10-01 07:01:00');
            INSERT INTO deliveries VALUES ('event','linear','delivered',2,'synthetic-object',3,'synthetic retry','2026-10-01 07:01:00');
        """)
        db.commit()
    return path


def test_snapshot_read_is_unchanged_and_preserves_values(snapshot_path):
    original = snapshot_path.read_bytes()
    snapshot = migration.read_snapshot(snapshot_path)
    assert snapshot_path.read_bytes() == original
    assert len(snapshot) == 11
    assert snapshot['task'][0]['id'] == 'legacy-task'
    assert snapshot['task'][0]['version'] == 3
    assert str(snapshot['gmail_mailbox'][0]['history_id']) == '18446744073709551615'
    assert snapshot['gmail_mailbox'][0]['updated_at'].hour == 7
    assert snapshot['delivery'][0]['updated_at'].utcoffset().total_seconds() == 0


def test_json_comparison_distinguishes_boolean_from_number():
    assert not migration._equal({'payload_json': {'nested': [True]}},
                                {'payload_json': {'nested': [1]}})
    assert migration._equal({'payload_json': {'a': 1, 'b': True}},
                            {'payload_json': {'b': True, 'a': 1}})


@pytest.mark.parametrize('statement', [
    'CREATE TABLE unexpected (id INTEGER)',
    'ALTER TABLE tasks ADD COLUMN unexpected TEXT',
    "UPDATE task_sources SET task_id='orphan'",
    "UPDATE outbox SET payload_json='{\"x\":1,\"x\":2}'",
    "UPDATE outbox SET payload_json='{\"x\":NaN}'",
    "UPDATE outbox SET payload_json='{\"x\":1e999}'",
    "UPDATE outbox SET payload_json='{\"x\":0.1234567890123456789}'",
    "UPDATE tasks SET due_date='not-a-date'",
    'UPDATE review_queue SET resolved=2',
])
def test_rejects_unsupported_source_without_private_values(snapshot_path, statement):
    with closing(sqlite3.connect(snapshot_path)) as db:
        db.execute(statement)
        db.commit()
    with pytest.raises(migration.MigrationError) as error:
        migration.read_snapshot(snapshot_path)
    assert 'synthetic-email' not in str(error.value)
    assert 'not-a-date' not in str(error.value)


@pytest.mark.usefixtures('empty_database')
def test_import_twice_compare_and_identity_continuation(engine, snapshot_path):
    snapshot = migration.read_snapshot(snapshot_path)
    for _ in range(2):
        assert set(migration.transfer(engine, snapshot, apply=True)['verified_counts'].values()) == {1}
    assert migration.transfer(engine, snapshot)['mode'] == 'compare'
    with engine.begin() as db:
        assert db.scalar(text("SELECT version FROM task")) == 3
        assert db.scalar(text("SELECT last_synced_version FROM destination_state")) == 3
        assert db.scalar(text("INSERT INTO email_event (source,external_id,received_at,processed_at) VALUES ('gmail','new',now(),now()) RETURNING id")) == 42
        assert db.scalar(text("INSERT INTO gmail_notification (mailbox,pubsub_message_id,history_id,received_at) VALUES ('me','new','1',now()) RETURNING id")) == 24


@pytest.mark.usefixtures('empty_database')
def test_resumes_matching_partial_import(engine, snapshot_path):
    snapshot = migration.read_snapshot(snapshot_path)
    partial = {table: [] for table in snapshot}
    partial['gmail_mailbox'] = snapshot['gmail_mailbox']
    migration.transfer(engine, partial, apply=True)
    migration.transfer(engine, snapshot, apply=True)
    migration.transfer(engine, snapshot)


@pytest.mark.usefixtures('empty_database')
@pytest.mark.parametrize('conflict', ['changed', 'extra', 'lease'])
def test_conflicting_target_is_not_overwritten(engine, snapshot_path, conflict):
    snapshot = migration.read_snapshot(snapshot_path)
    migration.transfer(engine, snapshot, apply=True)
    with engine.begin() as db:
        if conflict == 'changed':
            db.execute(text("UPDATE task SET title='Target edit'"))
        elif conflict == 'extra':
            db.execute(text("INSERT INTO gmail_mailbox VALUES ('extra',1,now())"))
        else:
            db.execute(text("UPDATE outbox SET lease_owner='worker',lease_until=now()"))
    for apply in (False, True):
        with pytest.raises(migration.MigrationError):
            migration.transfer(engine, snapshot, apply=apply)
    with engine.connect() as db:
        if conflict == 'changed':
            assert db.scalar(text('SELECT title FROM task')) == 'Target edit'
        elif conflict == 'extra':
            assert db.scalar(text('SELECT count(*) FROM gmail_mailbox')) == 2
        else:
            assert db.scalar(text('SELECT lease_owner FROM outbox')) == 'worker'


@pytest.mark.usefixtures('empty_database')
def test_late_foreign_key_failure_rolls_back_all_tables(engine, snapshot_path):
    snapshot = migration.read_snapshot(snapshot_path)
    snapshot['delivery'][0]['event_id'] = 'missing-event'
    with pytest.raises(migration.MigrationError):
        migration.transfer(engine, snapshot, apply=True)
    with engine.connect() as db:
        assert all(db.scalar(text(f'SELECT count(*) FROM {table}')) == 0 for table in TABLES)


@pytest.mark.usefixtures('empty_database')
def test_compare_never_populates_empty_target(engine, snapshot_path):
    with pytest.raises(migration.MigrationError):
        migration.transfer(engine, migration.read_snapshot(snapshot_path))
    with engine.connect() as db:
        assert all(db.scalar(text(f'SELECT count(*) FROM {table}')) == 0 for table in TABLES)


@pytest.mark.usefixtures('empty_database')
def test_ingestion_after_import_preserves_legacy_identity_and_advances_version(engine, snapshot_path):
    from datetime import datetime, timezone
    from postgres.canonical import CanonicalStore
    from workflow_engine.logic import Validator
    from workflow_engine.models import EmailMessage, TaskCandidate

    migration.transfer(engine, migration.read_snapshot(snapshot_path), apply=True)
    store = CanonicalStore(engine)
    message = EmailMessage('gmail', 'followup', 'thread', 'qa@example.invalid',
                           'Synthetic', '', datetime(2026, 10, 2, tzinfo=timezone.utc))
    candidate = TaskCandidate('Synthetic task', 'QA', 90, 'critical', None,
                              '2026-10-05', 0.9, True, 'synthetic-key')
    assert store.ingest(message, [candidate], Validator()) is True
    assert store.ingest(message, [candidate], Validator()) is False
    with engine.connect() as db:
        task = db.execute(text('SELECT id,version FROM task')).one()
        assert tuple(task) == ('legacy-task', 4)
        assert db.scalar(text("SELECT count(*) FROM outbox WHERE event_key='task:legacy-task:v:4'")) == 1
        assert db.scalar(text('SELECT last_synced_version FROM destination_state')) == 3
        assert db.scalar(text('SELECT count(*) FROM task_source')) == 2
