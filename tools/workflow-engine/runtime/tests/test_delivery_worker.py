import json

import aiosqlite
import pytest

import workflow_engine.delivery_worker as worker
from workflow_engine.db import SCHEMA as CORE_SCHEMA


async def _build_db(tmp_path):
    db = await aiosqlite.connect(
        str(tmp_path / "workflow.db")
    )
    await db.executescript(CORE_SCHEMA)
    await db.executescript(worker.SCHEMA)

    await db.execute(
        """
        INSERT INTO tasks (
            id,
            dedupe_key,
            title,
            project,
            priority_score,
            priority,
            owner,
            due_date,
            status,
            confidence,
            version,
            updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "task-1",
            "dedupe-1",
            "Deploy API",
            "Workflow Engine",
            90,
            "high",
            None,
            "2026-09-27",
            "open",
            0.99,
            1,
            "2026-09-26T00:00:00+00:00",
        ),
    )

    await db.execute(
        """
        INSERT INTO outbox (
            id,
            event_key,
            event_type,
            payload_json,
            status,
            attempts,
            created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "event-1",
            "task:task-1:v:1",
            "TASK_CHANGED",
            json.dumps({
                "task_id": "task-1",
                "version": 1,
            }),
            "pending",
            0,
            "2026-09-26T00:00:00+00:00",
        ),
    )

    await db.commit()
    return db


@pytest.mark.asyncio
async def test_linear_create_recovers_after_post_create_crash(
    tmp_path,
    monkeypatch,
):
    db = await _build_db(tmp_path)

    monkeypatch.setattr(
        worker,
        "LINEAR_MODE",
        "live",
    )
    monkeypatch.setattr(
        worker,
        "LINEAR_API_KEY",
        "test-key",
    )
    monkeypatch.setattr(
        worker,
        "LINEAR_TEAM_ID",
        "team-1",
    )

    remote = {}
    calls = {
        "find": 0,
        "create": 0,
        "update": 0,
    }

    async def fake_linear_request(
        query,
        variables,
    ):
        if "WorkflowIssueByMarker" in query:
            calls["find"] += 1
            marker = variables["marker"]
            nodes = [
                issue
                for issue in remote.values()
                if marker
                in issue["description"]
            ]
            return {
                "issues": {
                    "nodes": nodes[:2],
                }
            }

        if "WorkflowIssueCreate" in query:
            calls["create"] += 1
            issue = {
                "id": "linear-1",
                "identifier": "WE-1",
                "title": variables["title"],
                "description": variables[
                    "description"
                ],
            }
            remote[issue["id"]] = issue
            return {
                "issueCreate": {
                    "success": True,
                    "issue": issue,
                }
            }

        if "WorkflowIssueUpdate" in query:
            calls["update"] += 1
            issue = remote[
                variables["id"]
            ]
            issue["title"] = variables[
                "title"
            ]
            issue["description"] = variables[
                "description"
            ]
            return {
                "issueUpdate": {
                    "success": True,
                    "issue": issue,
                }
            }

        raise AssertionError(
            "unexpected Linear query"
        )

    monkeypatch.setattr(
        worker,
        "linear_request",
        fake_linear_request,
    )

    await worker.seed_deliveries(db)

    original_save_mapping = (
        worker._save_mapping
    )
    crashed = False

    async def crash_after_create(
        db_arg,
        **kwargs,
    ):
        nonlocal crashed

        if not crashed:
            crashed = True
            raise RuntimeError(
                "simulated post-create crash"
            )

        return await original_save_mapping(
            db_arg,
            **kwargs,
        )

    monkeypatch.setattr(
        worker,
        "_save_mapping",
        crash_after_create,
    )

    await worker.sync_linear_live(db)

    assert calls["create"] == 1

    cur = await db.execute(
        """
        SELECT external_object_id
        FROM destination_objects
        WHERE task_id = 'task-1'
          AND destination = 'linear'
        """
    )
    assert await cur.fetchone() is None

    cur = await db.execute(
        """
        SELECT status, idempotency_marker
        FROM delivery_intents
        WHERE task_id = 'task-1'
          AND destination = 'linear'
        """
    )
    intent = await cur.fetchone()
    assert intent[0] == "creating"
    assert (
        intent[1]
        == "workflow-engine-task:task-1"
    )

    monkeypatch.setattr(
        worker,
        "_save_mapping",
        original_save_mapping,
    )

    await worker.sync_linear_live(db)

    assert calls["create"] == 1
    assert calls["find"] >= 2

    cur = await db.execute(
        """
        SELECT
            external_object_id,
            last_synced_version
        FROM destination_objects
        WHERE task_id = 'task-1'
          AND destination = 'linear'
        """
    )
    mapping = await cur.fetchone()
    assert mapping == ("linear-1", 1)

    cur = await db.execute(
        """
        SELECT status, external_object_id
        FROM delivery_intents
        WHERE task_id = 'task-1'
          AND destination = 'linear'
        """
    )
    intent = await cur.fetchone()
    assert intent == (
        "resolved",
        "linear-1",
    )

    await db.execute(
        """
        UPDATE tasks
        SET
            version = 2,
            title = 'Deploy API v2',
            updated_at = ?
        WHERE id = 'task-1'
        """,
        ("2026-09-26T01:00:00+00:00",),
    )

    await db.execute(
        """
        INSERT INTO outbox (
            id,
            event_key,
            event_type,
            payload_json,
            status,
            attempts,
            created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "event-2",
            "task:task-1:v:2",
            "TASK_CHANGED",
            json.dumps({
                "task_id": "task-1",
                "version": 2,
            }),
            "pending",
            0,
            "2026-09-26T01:00:00+00:00",
        ),
    )
    await db.commit()

    await worker.seed_deliveries(db)
    await worker.sync_linear_live(db)

    assert calls["create"] == 1
    assert calls["update"] == 1

    cur = await db.execute(
        """
        SELECT
            external_object_id,
            last_synced_version
        FROM destination_objects
        WHERE task_id = 'task-1'
          AND destination = 'linear'
        """
    )
    mapping = await cur.fetchone()
    assert mapping == ("linear-1", 2)

    await db.close()


def test_linear_description_has_stable_marker():
    description = worker.task_description(
        task_id="task-123",
        version=4,
        project="Workflow Engine",
        priority="high",
        due_date=None,
    )

    assert (
        "workflow-engine-task:task-123"
        in description
    )
