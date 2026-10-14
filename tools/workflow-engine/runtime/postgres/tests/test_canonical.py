from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timezone
from threading import Barrier
import uuid

import pytest
from sqlalchemy import text

from postgres.canonical import CanonicalStore, CanonicalWriteError
from workflow_engine.logic import Validator
from workflow_engine.models import EmailMessage, TaskCandidate

pytestmark = pytest.mark.usefixtures('empty_database')


def message(external_id='synthetic-email'):
    return EmailMessage('gmail', external_id, 'synthetic-thread', 'qa@example.invalid',
                        'Synthetic request', '', datetime(2026, 10, 2, tzinfo=timezone.utc))


def candidate(key='synthetic-key', **changes):
    return replace(TaskCandidate('Synthetic task', 'QA', 50, 'normal', None, None,
                                 0.85, False, key), **changes)


def rows(engine, table):
    with engine.connect() as db:
        return [dict(row) for row in db.execute(text(f'SELECT * FROM {table}')).mappings()]


def test_repeated_email_is_immutable_and_has_one_task_and_event(engine):
    store = CanonicalStore(engine)
    assert store.ingest(message(), [candidate()], Validator()) is True
    before = {table: rows(engine, table) for table in ('email_event', 'task', 'task_source', 'outbox')}
    assert store.ingest(replace(message(), subject='Changed replay'), [candidate(priority_score=99)], Validator()) is False
    assert {table: rows(engine, table) for table in before} == before
    task = before['task'][0]
    assert task['id'] == str(uuid.uuid5(uuid.NAMESPACE_URL, 'workflow-engine:task:synthetic-key'))
    assert task['version'] == 1
    assert before['outbox'][0]['event_key'] == f"task:{task['id']}:v:1"


def test_followup_merges_date_priority_and_keeps_identity(engine):
    store = CanonicalStore(engine)
    store.ingest(message(), [candidate(due_date='2026-10-05')], Validator())
    first = rows(engine, 'task')[0]
    store.ingest(message('followup'), [candidate(due_date='2026-10-05', priority_score=90)], Validator())
    task = rows(engine, 'task')[0]
    assert task['id'] == first['id']
    assert (task['version'], task['priority_score'], task['priority']) == (2, 90, 'critical')
    assert len(rows(engine, 'review_queue')) == 0
    assert {row['payload_json']['version'] for row in rows(engine, 'outbox')} == {1, 2}


@pytest.mark.parametrize('changes', [{'confidence': 0.4}, {'due_date': '2026-10-06'}])
def test_review_preserves_existing_task_without_new_event(engine, changes):
    store = CanonicalStore(engine)
    store.ingest(message(), [candidate(due_date='2026-10-05')], Validator())
    before = rows(engine, 'task')
    store.ingest(message('review-email'), [candidate(**changes)], Validator())
    assert rows(engine, 'task') == before
    assert len(rows(engine, 'outbox')) == 1
    assert len(rows(engine, 'task_source')) == 1
    assert len(rows(engine, 'email_event')) == 2
    assert len(rows(engine, 'review_queue')) == 1
    assert store.ingest(message('review-email'), [candidate(**changes)], Validator()) is False
    assert len(rows(engine, 'review_queue')) == 1


@pytest.mark.parametrize('existing', [False, True])
def test_outbox_constraint_failure_rolls_back_email_task_source_and_review(engine, existing):
    store = CanonicalStore(engine)
    if existing:
        store.ingest(message('previous'), [candidate()], Validator())
    before = {table: rows(engine, table) for table in ('email_event', 'task', 'task_source', 'outbox', 'review_queue')}
    # Real database constraint failure after task/source/review writes.
    with engine.begin() as db:
        db.execute(text("ALTER TABLE outbox ADD CONSTRAINT synthetic_failure CHECK (event_type <> 'TASK_CHANGED') NOT VALID"))
    try:
        with pytest.raises(CanonicalWriteError) as error:
            store.ingest(message(), [candidate('review', confidence=0.1), candidate()], Validator())
        assert str(error.value) == 'Canonical ingestion failed; transaction rolled back'
        assert {table: rows(engine, table) for table in before} == before
    finally:
        with engine.begin() as db:
            db.execute(text('ALTER TABLE outbox DROP CONSTRAINT synthetic_failure'))
    assert store.ingest(message(), [candidate()], Validator()) is True


@pytest.mark.parametrize('same_message', [False, True])
def test_concurrent_ingestion_preserves_versions_and_source_uniqueness(engine, same_message):
    barrier = Barrier(4)
    def ingest(index):
        barrier.wait(timeout=10)
        return CanonicalStore(engine).ingest(message('same' if same_message else f'email-{index}'),
                                            [candidate()], Validator())
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(ingest, index) for index in range(4)]
        results = [future.result(timeout=20) for future in futures]
    expected = 1 if same_message else 4
    assert sum(results) == expected
    assert len(rows(engine, 'task')) == 1
    assert rows(engine, 'task')[0]['version'] == expected
    for table in ('email_event', 'task_source', 'outbox'):
        assert len(rows(engine, table)) == expected
    assert {row['payload_json']['version'] for row in rows(engine, 'outbox')} == set(range(1, expected + 1))


def test_reversed_candidate_order_does_not_deadlock(engine):
    barrier = Barrier(2)
    def ingest(index):
        keys = ['first', 'second'] if index == 0 else ['second', 'first']
        barrier.wait(timeout=10)
        return CanonicalStore(engine).ingest(message(f'email-{index}'), [candidate(key) for key in keys], Validator())
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(ingest, index) for index in range(2)]
        assert all(future.result(timeout=20) for future in futures)
    assert {row['version'] for row in rows(engine, 'task')} == {2}
    assert len(rows(engine, 'outbox')) == 4


def test_duplicate_candidate_batch_is_rejected_before_writing(engine):
    with pytest.raises(ValueError, match='Duplicate candidate'):
        CanonicalStore(engine).ingest(message(), [candidate(), candidate()], Validator())
    assert rows(engine, 'email_event') == []


def test_email_without_candidates_is_recorded_once(engine):
    store = CanonicalStore(engine)
    assert store.ingest(message(), [], Validator())
    assert not store.ingest(message(), [], Validator())
    assert len(rows(engine, 'email_event')) == 1
    assert rows(engine, 'task') == rows(engine, 'outbox') == []
