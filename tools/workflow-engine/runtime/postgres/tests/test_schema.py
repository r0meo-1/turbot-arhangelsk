import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import time

from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError, IntegrityError


ROOT = Path(__file__).resolve().parents[1]
TABLES = {
    "gmail_mailbox", "gmail_watch", "gmail_notification", "email_event",
    "extraction_run", "task", "task_source", "review_queue", "outbox",
    "destination_state", "delivery_intent", "delivery", "dead_letter",
}


@pytest.fixture(scope="module")
def engine():
    url = os.environ.get("WORKFLOW_POSTGRES_MIGRATION_URL")
    if not url:
        pytest.skip("Separate PostgreSQL test database not configured")
    parsed = make_url(url)
    if parsed.host not in {"localhost", "127.0.0.1"} or not (parsed.database or "").endswith("_test"):
        pytest.fail("Schema tests require a loopback database ending in _test")
    command.upgrade(Config(str(ROOT / "alembic.ini")), "head")
    instance = create_engine(url, hide_parameters=True)
    yield instance
    instance.dispose()


@pytest.fixture(autouse=True)
def empty_database(engine):
    with engine.begin() as connection:
        connection.execute(text("TRUNCATE " + ", ".join(sorted(TABLES)) + " RESTART IDENTITY CASCADE"))
    yield


def task(connection, task_id="synthetic-task", key="synthetic-key"):
    connection.execute(text("""
        INSERT INTO task (id,dedupe_key,title,project,priority_score,priority,confidence,updated_at)
        VALUES (:id,:key,'Synthetic task','QA',50,'P2',0.85,now())
    """), {"id": task_id, "key": key})


def event(connection, event_id="synthetic-event", event_key="task:synthetic-task:v:1"):
    connection.execute(text("""
        INSERT INTO outbox (id,event_key,event_type,payload_json,created_at)
        VALUES (:id,:key,'TASK_CHANGED',CAST(:payload AS JSONB),now())
    """), {"id": event_id, "key": event_key,
             "payload": '{"task_id":"synthetic-task","version":1}'})


def test_upgrade_repeat_empty_downgrade_and_reupgrade(engine):
    config = Config(str(ROOT / "alembic.ini"))
    assert set(inspect(engine).get_table_names()) == TABLES | {"alembic_version"}
    command.upgrade(config, "head")
    command.downgrade(config, "base")
    assert inspect(engine).get_table_names() == ["alembic_version"]
    command.upgrade(config, "head")
    assert set(inspect(engine).get_table_names()) == TABLES | {"alembic_version"}


def test_populated_downgrade_fails_without_losing_rows_or_revision(engine):
    with engine.begin() as connection:
        task(connection)
    with pytest.raises(DBAPIError, match="Refusing downgrade"):
        command.downgrade(Config(str(ROOT / "alembic.ini")), "base")
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM task")) == 1
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "20261001_01"


def test_downgrade_waits_for_concurrent_writer_then_preserves_committed_row(engine):
    with engine.connect() as writer, ThreadPoolExecutor(max_workers=1) as pool:
        transaction = writer.begin()
        task(writer)
        future = pool.submit(command.downgrade, Config(str(ROOT / "alembic.ini")), "base")
        try:
            deadline = time.monotonic() + 10
            waiting = False
            while time.monotonic() < deadline:
                with engine.connect() as observer:
                    waiting = observer.scalar(text("""
                        SELECT EXISTS (
                            SELECT 1 FROM pg_locks
                            WHERE relation = 'task'::regclass
                              AND mode = 'AccessExclusiveLock' AND NOT granted
                        )
                    """))
                if waiting or future.done():
                    break
                time.sleep(0.02)
            assert waiting, "Downgrade must wait for the in-flight canonical writer"
            transaction.commit()
            with pytest.raises(DBAPIError, match="Refusing downgrade"):
                future.result(timeout=10)
        finally:
            if transaction.is_active:
                transaction.rollback()
        with engine.connect() as observer:
            assert observer.scalar(text("SELECT count(*) FROM task")) == 1
            assert observer.scalar(text("SELECT version_num FROM alembic_version")) == "20261001_01"


def test_email_source_deduplication_and_immutability(engine):
    insert = text("""
        INSERT INTO email_event (source,external_id,received_at,processed_at)
        VALUES ('gmail','synthetic-email',now(),now())
    """)
    with engine.begin() as connection:
        connection.execute(insert)
    with pytest.raises(IntegrityError):
        with engine.begin() as connection:
            connection.execute(insert)
    for statement in ("UPDATE email_event SET subject='changed'", "DELETE FROM email_event"):
        with pytest.raises(DBAPIError, match="email_event is immutable"):
            with engine.begin() as connection:
                connection.execute(text(statement))
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM email_event")) == 1


def test_notification_duplicate_and_large_history_precision(engine):
    insert = text("""
        INSERT INTO gmail_notification (mailbox,pubsub_message_id,history_id,received_at)
        VALUES ('primary',:message_id,18446744073709551615,now())
    """)
    with engine.begin() as connection:
        connection.execute(insert, {"message_id": "first"})
    with pytest.raises(IntegrityError):
        with engine.begin() as connection:
            connection.execute(insert, {"message_id": "repeat"})
    with engine.connect() as connection:
        assert str(connection.scalar(text("SELECT history_id FROM gmail_notification"))) == "18446744073709551615"


def test_task_and_outbox_rollback_after_real_constraint_failure(engine):
    with engine.begin() as connection:
        event(connection, event_id="prior", event_key="collision")
    with pytest.raises(IntegrityError):
        with engine.begin() as connection:
            task(connection)
            connection.execute(text("""
                INSERT INTO task_source (task_id,source,source_key,external_id)
                VALUES ('synthetic-task','gmail','synthetic-key','synthetic-email')
            """))
            event(connection, event_key="collision")
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM task")) == 0
        assert connection.scalar(text("SELECT count(*) FROM task_source")) == 0
        assert connection.scalar(text("SELECT count(*) FROM outbox")) == 1


def test_source_destination_and_outbox_uniqueness(engine):
    with engine.begin() as connection:
        task(connection)
        event(connection)
        connection.execute(text("""
            INSERT INTO destination_state (task_id,destination,external_object_id,updated_at)
            VALUES ('synthetic-task','linear','synthetic-object',now())
        """))
    with pytest.raises(IntegrityError):
        with engine.begin() as connection:
            event(connection, event_id="different-row")
    with pytest.raises(IntegrityError):
        with engine.begin() as connection:
            task(connection, task_id="different-task", key="different-key")
            connection.execute(text("""
                INSERT INTO destination_state (task_id,destination,external_object_id,updated_at)
                VALUES ('different-task','linear','synthetic-object',now())
            """))
    with engine.begin() as connection:
        connection.execute(text("""
            INSERT INTO task_source (task_id,source,source_key,external_id)
            VALUES ('synthetic-task','gmail','synthetic-key','synthetic-email')
        """))
    with pytest.raises(IntegrityError):
        with engine.begin() as connection:
            connection.execute(text("""
                INSERT INTO task_source (task_id,source,source_key,external_id)
                VALUES ('synthetic-task','gmail','synthetic-key','different-email')
            """))


def test_skip_locked_claims_different_rows_and_rollback_releases_lock(engine):
    with engine.begin() as connection:
        event(connection, event_id="a", event_key="a")
        event(connection, event_id="b", event_key="b")
    query = text("SELECT id FROM outbox ORDER BY id LIMIT 1 FOR UPDATE SKIP LOCKED")
    with engine.connect() as first, engine.connect() as second:
        first_tx = first.begin()
        second_tx = second.begin()
        try:
            assert first.scalar(query) == "a"
            assert second.scalar(query) == "b"
            first_tx.rollback()
            assert second.scalar(query) == "a"
        finally:
            if first_tx.is_active:
                first_tx.rollback()
            second_tx.rollback()
