import asyncio
from threading import Event

import pytest
from sqlalchemy import text

from postgres.worker import MarkdownWorker, PROJECTION_LOCK, ProjectionDeliveryError
from workflow_engine.projection import MarkdownProjection, START


@pytest.fixture
def worker(engine, empty_database, tmp_path):
    with engine.begin() as db:
        db.execute(text("""INSERT INTO outbox
            (id,event_key,event_type,payload_json,created_at)
            VALUES ('event','synthetic','TASK_CHANGED','{}',now())"""))
    return MarkdownWorker(engine, MarkdownProjection(str(tmp_path / 'board.md')))


def status(engine):
    with engine.connect() as db:
        return db.execute(text("SELECT status,last_error FROM outbox WHERE id='event'")).one()


def expire(engine):
    with engine.begin() as db:
        db.execute(text("UPDATE outbox SET lease_until=clock_timestamp()-interval '1 second'"))


def test_deliver_and_idle(worker, engine):
    assert asyncio.run(worker.deliver_one_async()) == 'delivered'
    assert status(engine) == ('done', None)
    assert worker.deliver_one() == 'idle'
    assert worker.projection.path.read_text().count(START) == 1


def test_busy_destination_does_not_claim(worker, engine):
    with engine.begin() as db:
        db.execute(text('SELECT pg_advisory_xact_lock(:key)'), {'key': PROJECTION_LOCK})
        assert worker.deliver_one() == 'busy'
    assert status(engine)[0] == 'pending'


def test_expired_before_write_never_publishes(worker, engine, monkeypatch):
    original = worker.projection.render
    def render(*args):
        result = original(*args)
        expire(engine)
        return result
    monkeypatch.setattr(worker.projection, 'render', render)
    assert worker.deliver_one() == 'lease_lost'
    assert not worker.projection.path.exists()
    assert status(engine)[0] == 'processing'


def test_write_success_ack_expiry_replays_without_duplicate_block(worker, engine, monkeypatch):
    original = worker.projection.write
    def write(value):
        original(value)
        expire(engine)
    monkeypatch.setattr(worker.projection, 'write', write)
    assert worker.deliver_one() == 'lease_lost'
    monkeypatch.setattr(worker.projection, 'write', original)
    assert worker.deliver_one() == 'delivered'
    assert worker.projection.path.read_text().count(START) == 1


def test_write_failure_is_sanitized_and_retryable(worker, engine, monkeypatch):
    def fail(value):
        raise OSError('private path and message content')
    monkeypatch.setattr(worker.projection, 'write', fail)
    with pytest.raises(ProjectionDeliveryError) as error:
        worker.deliver_one()
    assert str(error.value) == 'Markdown projection delivery failed'
    assert status(engine) == ('pending', 'projection_failed')


def test_async_cancellation_waits_for_background_write(worker, engine, monkeypatch):
    entered, release = Event(), Event()
    original = worker.projection.write
    def write(value):
        entered.set()
        assert release.wait(10)
        original(value)
    monkeypatch.setattr(worker.projection, 'write', write)
    async def scenario():
        task = asyncio.create_task(worker.deliver_one_async())
        try:
            assert await asyncio.to_thread(entered.wait, 10)
            task.cancel()
            await asyncio.sleep(0)
            assert not task.done()
            assert await asyncio.to_thread(worker.deliver_one) == 'busy'
        finally:
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await task
    asyncio.run(scenario())
    assert status(engine)[0] == 'done'
