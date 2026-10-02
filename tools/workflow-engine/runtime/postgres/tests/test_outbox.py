from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from sqlalchemy import text

from postgres.outbox import MAX_ATTEMPTS, OutboxStore, OutboxStateError, _duration


@pytest.mark.parametrize('value', [0, -1, 3601, True, 1.5, '60'])
def test_invalid_lease_duration(value):
    with pytest.raises(ValueError):
        _duration(value)


def seed(engine, event_id='event'):
    with engine.begin() as db:
        db.execute(text('''
            INSERT INTO outbox (id,event_key,event_type,payload_json,created_at)
            VALUES (:id,:id,'TASK_CHANGED',CAST(:payload AS JSONB),now())
        '''), {'id': event_id, 'payload': '{"task_id":"synthetic","version":1}'})


def row(engine):
    with engine.connect() as db:
        return dict(db.execute(text("SELECT * FROM outbox WHERE id='event'")).mappings().one())


def expire(engine):
    with engine.begin() as db:
        db.execute(text("UPDATE outbox SET lease_until=clock_timestamp()-interval '1 second' WHERE id='event'"))


@pytest.fixture
def queue(engine, empty_database):
    seed(engine)
    return OutboxStore(engine)


def test_claim_renew_complete_and_replay(queue, engine):
    event = queue.claim(60)
    assert (event['status'], event['attempts']) == ('processing', 1)
    assert queue.claim() is None
    assert queue.renew(event['id'], event['lease_owner'], 120)
    assert row(engine)['lease_until'] > event['lease_until']
    extended = row(engine)['lease_until']
    assert queue.renew(event['id'], event['lease_owner'], 1)
    assert row(engine)['lease_until'] >= extended
    assert queue.complete(event['id'], event['lease_owner'])
    assert not queue.complete(event['id'], event['lease_owner'])
    assert queue.claim() is None
    assert row(engine)['status'] == 'done'
    assert row(engine)['lease_owner'] is row(engine)['lease_until'] is None


def test_skip_locked_takes_next_event(queue, engine):
    seed(engine, 'second')
    with engine.begin() as blocking:
        blocking.execute(text("SELECT id FROM outbox WHERE id='event' FOR UPDATE"))
        event = queue.claim()
        assert event['id'] == 'second'
    assert queue.claim()['id'] == 'event'


@pytest.mark.parametrize('event_count', [1, 4])
def test_concurrent_claims_never_share_an_event(queue, engine, event_count):
    for index in range(1, event_count):
        seed(engine, f'event-{index}')
    barrier = Barrier(4)
    def claim():
        barrier.wait(timeout=10)
        return OutboxStore(engine).claim()
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(claim) for _ in range(4)]
        events = [future.result(timeout=20) for future in futures]
    claimed = [event for event in events if event]
    assert len(claimed) == event_count
    assert len({event['id'] for event in claimed}) == event_count
    assert len({event['lease_owner'] for event in claimed}) == event_count
    assert {event['attempts'] for event in claimed} == {1}


@pytest.mark.parametrize('operation', ['renew', 'complete', 'fail'])
def test_expired_or_superseded_token_cannot_mutate_event(queue, engine, operation):
    first = queue.claim()
    expire(engine)
    assert not getattr(queue, operation)(first['id'], first['lease_owner'])
    recovered = OutboxStore(engine).claim()
    assert recovered['lease_owner'] != first['lease_owner']
    assert recovered['attempts'] == 2
    before = row(engine)
    assert not getattr(queue, operation)(first['id'], first['lease_owner'])
    assert row(engine) == before
    assert queue.complete(recovered['id'], recovered['lease_owner'])


def test_failures_reach_dead_letter_and_explicit_retry_has_fresh_budget(queue, engine):
    for attempt in range(1, MAX_ATTEMPTS + 1):
        event = queue.claim()
        assert event['attempts'] == attempt
        assert queue.fail(event['id'], event['lease_owner'], 'projection_failed')
    assert queue.claim() is None
    assert row(engine)['status'] == 'dead'
    with engine.connect() as db:
        failure = db.execute(text('SELECT * FROM dead_letter')).mappings().one()
        assert (failure['reason'], failure['attempts'], failure['retried_at']) == ('projection_failed', 8, None)
    assert queue.retry_dead('event')
    assert not queue.retry_dead('event')
    fresh = queue.claim()
    assert fresh['attempts'] == 1
    assert not queue.complete('event', event['lease_owner'])
    assert queue.complete('event', fresh['lease_owner'])
    with engine.connect() as db:
        assert db.scalar(text('SELECT retried_at FROM dead_letter')) is not None


def test_crashed_final_attempt_recovers_to_dead_letter(queue, engine):
    for _ in range(MAX_ATTEMPTS):
        event = queue.claim()
        assert event is not None
        assert queue.claim() is None  # Active attempt is not stolen.
        expire(engine)
    assert queue.claim() is None
    assert row(engine)['status'] == 'dead'
    with engine.connect() as db:
        assert db.scalar(text('SELECT reason FROM dead_letter')) == 'attempts_exhausted'
        assert db.scalar(text('SELECT attempts FROM dead_letter')) == MAX_ATTEMPTS
    assert queue.retry_dead('event')
    assert queue.claim()['attempts'] == 1


def test_dead_letter_insert_failure_rolls_back_failed_event(queue, engine):
    with engine.begin() as db:
        db.execute(text("UPDATE outbox SET attempts=7 WHERE id='event'"))
        db.execute(text("ALTER TABLE dead_letter ADD CONSTRAINT synthetic_failure CHECK (reason <> 'delivery_failed')"))
    event = queue.claim()
    before = row(engine)
    try:
        with pytest.raises(OutboxStateError, match='transaction rolled back'):
            queue.fail(event['id'], event['lease_owner'])
        assert row(engine) == before
    finally:
        with engine.begin() as db:
            db.execute(text('ALTER TABLE dead_letter DROP CONSTRAINT synthetic_failure'))
    assert queue.fail(event['id'], event['lease_owner'])
    assert row(engine)['status'] == 'dead'


def test_retry_marker_failure_rolls_back_reset(queue, engine):
    with engine.begin() as db:
        db.execute(text("UPDATE outbox SET attempts=8 WHERE id='event'"))
    assert queue.claim() is None
    with engine.begin() as db:
        db.execute(text('ALTER TABLE dead_letter ADD CONSTRAINT synthetic_retry CHECK (retried_at IS NULL)'))
    try:
        with pytest.raises(OutboxStateError):
            queue.retry_dead('event')
        assert (row(engine)['status'], row(engine)['attempts']) == ('dead', 8)
    finally:
        with engine.begin() as db:
            db.execute(text('ALTER TABLE dead_letter DROP CONSTRAINT synthetic_retry'))
    assert queue.retry_dead('event')


def test_raw_error_text_is_rejected_without_mutation(queue, engine):
    event = queue.claim()
    before = row(engine)
    with pytest.raises(ValueError, match='approved failure code'):
        queue.fail(event['id'], event['lease_owner'], 'Private source text must not be persisted')
    assert row(engine) == before


def test_retry_does_not_reset_pending_or_done_event(queue, engine):
    assert not queue.retry_dead('event')
    event = queue.claim()
    assert not queue.retry_dead('event')
    assert queue.complete(event['id'], event['lease_owner'])
    assert not queue.retry_dead('event')
    assert not queue.retry_dead('missing')


def test_repeated_dead_letter_cycle_updates_one_summary(queue, engine):
    for cycle in range(2):
        with engine.begin() as db:
            db.execute(text("UPDATE outbox SET attempts=7 WHERE id='event'"))
        event = queue.claim()
        assert queue.fail(event['id'], event['lease_owner'])
        with engine.connect() as db:
            assert db.scalar(text('SELECT count(*) FROM dead_letter')) == 1
            assert db.scalar(text('SELECT retried_at FROM dead_letter')) is None
        if cycle == 0:
            assert queue.retry_dead('event')
