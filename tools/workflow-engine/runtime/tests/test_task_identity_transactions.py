from datetime import datetime, timezone
import json
import sqlite3
import uuid

import pytest

from workflow_engine.db import Repository
from workflow_engine.logic import Validator
from workflow_engine.models import EmailMessage, TaskCandidate


def message(external_id="synthetic-message"):
    return EmailMessage(
        source="gmail", external_id=external_id, thread_id="synthetic-thread",
        sender="qa@example.test", subject="Synthetic request", body="",
        received_at=datetime(2026, 10, 1, tzinfo=timezone.utc),
    )


def candidate(key="synthetic-canonical-key"):
    return TaskCandidate(
        title="Synthetic request", project="QA", priority_score=50,
        priority="P2", owner=None, due_date=None, confidence=0.85,
        explicit_deadline_language=False, dedupe_key=key,
    )


async def rows(repo, table):
    cursor = await repo.db.execute(f"SELECT * FROM {table}")
    return [dict(row) for row in await cursor.fetchall()]


@pytest.mark.asyncio
async def test_fresh_database_replay_preserves_task_and_outbox_identity(tmp_path):
    identities = []
    for name in ("first", "replay"):
        repo = Repository(tmp_path / f"{name}.db")
        await repo.connect()
        try:
            await repo.ingest(message(), [candidate()], Validator())
            task = (await rows(repo, "tasks"))[0]
            event = (await rows(repo, "outbox"))[0]
            identities.append((task["id"], event["event_key"], json.loads(event["payload_json"])))
            assert uuid.UUID(task["id"]).version == 5
            await repo.ingest(message("distinct-request"), [candidate("distinct-key")], Validator())
            assert len({row["id"] for row in await rows(repo, "tasks")}) == 2
        finally:
            await repo.close()
    assert identities[0] == identities[1]


@pytest.mark.asyncio
async def test_existing_random_id_is_preserved_in_task_and_outbox(tmp_path):
    repo = Repository(tmp_path / "legacy.db")
    await repo.connect()
    legacy_id = str(uuid.uuid4())
    try:
        await repo.ingest(message(), [candidate()], Validator())
        # Model a pre-upgrade database whose canonical ID was random.
        await repo.db.execute("UPDATE tasks SET id = ?", (legacy_id,))
        await repo.db.execute("UPDATE task_sources SET task_id = ?", (legacy_id,))
        await repo.db.execute("DELETE FROM outbox")
        await repo.db.commit()
        await repo.ingest(message("follow-up"), [candidate()], Validator())
        tasks = await rows(repo, "tasks")
        assert len(tasks) == 1
        assert tasks[0]["id"] == legacy_id
        assert tasks[0]["version"] == 2
        event = (await rows(repo, "outbox"))[0]
        assert event["event_key"] == f"task:{legacy_id}:v:2"
        assert json.loads(event["payload_json"])["task_id"] == legacy_id
    finally:
        await repo.close()


@pytest.mark.asyncio
async def test_outbox_failure_rolls_back_email_task_and_source_then_replays(tmp_path):
    repo = Repository(tmp_path / "failure.db")
    await repo.connect()
    try:
        await repo.db.execute("""
            CREATE TRIGGER fail_outbox BEFORE INSERT ON outbox
            BEGIN SELECT RAISE(ABORT, 'synthetic outbox failure'); END
        """)
        await repo.db.commit()
        with pytest.raises(sqlite3.IntegrityError, match="synthetic outbox failure"):
            await repo.ingest(message(), [candidate()], Validator())
        for table in ("messages", "tasks", "task_sources", "outbox"):
            assert await rows(repo, table) == []
        await repo.db.execute("DROP TRIGGER fail_outbox")
        await repo.db.commit()
        assert await repo.ingest(message(), [candidate()], Validator()) is True
        for table in ("messages", "tasks", "task_sources", "outbox"):
            assert len(await rows(repo, table)) == 1
    finally:
        await repo.close()


@pytest.mark.asyncio
async def test_duplicate_email_does_not_mutate_task_or_create_outbox(tmp_path):
    repo = Repository(tmp_path / "duplicate.db")
    await repo.connect()
    try:
        await repo.ingest(message(), [candidate()], Validator())
        before = {table: await rows(repo, table) for table in ("messages", "tasks", "task_sources", "outbox")}
        assert await repo.ingest(message(), [candidate()], Validator()) is False
        assert {table: await rows(repo, table) for table in before} == before
    finally:
        await repo.close()
