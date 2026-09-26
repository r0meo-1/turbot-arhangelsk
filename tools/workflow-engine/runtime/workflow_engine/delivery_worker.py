from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import urllib.error
import urllib.request

import aiosqlite


DB_PATH = os.getenv(
    "WORKFLOW_DB",
    "/var/lib/workflow-engine/workflow.db",
)
POLL_SECONDS = int(
    os.getenv("DELIVERY_POLL_SECONDS", "10")
)
LINEAR_MODE = os.getenv(
    "LINEAR_MODE",
    "dry_run",
).strip().lower()
LINEAR_API_KEY = os.getenv(
    "LINEAR_API_KEY",
    "",
).strip()
LINEAR_TEAM_ID = os.getenv(
    "LINEAR_TEAM_ID",
    "",
).strip()
LINEAR_ENDPOINT = "https://api.linear.app/graphql"

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("delivery-worker")


SCHEMA = """
CREATE TABLE IF NOT EXISTS deliveries (
    event_id TEXT NOT NULL,
    destination TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    attempts INTEGER NOT NULL DEFAULT 0,
    external_object_id TEXT,
    last_synced_version INTEGER,
    last_error TEXT,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (event_id, destination)
);

CREATE INDEX IF NOT EXISTS idx_deliveries_pending
ON deliveries(destination, status);

CREATE TABLE IF NOT EXISTS destination_objects (
    task_id TEXT NOT NULL,
    destination TEXT NOT NULL,
    external_object_id TEXT,
    last_synced_version INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (task_id, destination)
);

CREATE TABLE IF NOT EXISTS delivery_intents (
    task_id TEXT NOT NULL,
    destination TEXT NOT NULL,
    idempotency_marker TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'creating',
    external_object_id TEXT,
    last_synced_version INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (task_id, destination),
    UNIQUE(destination, idempotency_marker)
);
"""


class LinearError(RuntimeError):
    pass


class LinearRegionBlocked(LinearError):
    """Permanent region restriction reported by Linear."""

    pass


def _linear_request_sync(
    query: str,
    variables: dict,
    *,
    attempts: int = 5,
) -> dict:
    if not LINEAR_API_KEY:
        raise LinearError("LINEAR_API_KEY is missing")

    payload = json.dumps(
        {
            "query": query,
            "variables": variables,
        }
    ).encode("utf-8")

    for attempt in range(attempts):
        request = urllib.request.Request(
            LINEAR_ENDPOINT,
            data=payload,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Authorization": LINEAR_API_KEY,
                "User-Agent": "workflow-engine/1.0",
            },
        )

        try:
            with urllib.request.urlopen(
                request,
                timeout=20,
            ) as response:
                body = response.read().decode("utf-8")
                data = json.loads(body)

            if data.get("errors"):
                messages = "; ".join(
                    str(item.get("message", "GraphQL error"))
                    for item in data["errors"]
                )
                raise LinearError(messages)

            return data.get("data") or {}

        except urllib.error.HTTPError as exc:
            retryable = (
                exc.code == 429
                or 500 <= exc.code <= 599
            )

            if not retryable or attempt == attempts - 1:
                detail = ""

                if exc.code == 403:
                    try:
                        detail = exc.read().decode(
                            "utf-8",
                            errors="replace",
                        )
                    except Exception:
                        detail = ""

                if (
                    exc.code == 403
                    and (
                        "RESTRICTED_COUNTRY_BLOCKED" in detail
                        or "not available in Russia" in detail
                    )
                ):
                    raise LinearRegionBlocked(
                        "Linear API blocked this execution region "
                        "(RESTRICTED_COUNTRY_BLOCKED, countryCode=RU)"
                    ) from exc

                raise LinearError(
                    f"Linear HTTP {exc.code}"
                ) from exc

        except urllib.error.URLError as exc:
            if attempt == attempts - 1:
                raise LinearError(
                    "Linear network request failed"
                ) from exc

        time.sleep(min(2 ** attempt, 15))

    raise LinearError("Linear request failed")


async def linear_request(
    query: str,
    variables: dict,
) -> dict:
    return await asyncio.to_thread(
        _linear_request_sync,
        query,
        variables,
    )


CREATE_ISSUE = """
mutation WorkflowIssueCreate(
  $teamId: String!,
  $title: String!,
  $description: String
) {
  issueCreate(
    input: {
      teamId: $teamId,
      title: $title,
      description: $description
    }
  ) {
    success
    issue {
      id
      identifier
      title
    }
  }
}
"""


UPDATE_ISSUE = """
mutation WorkflowIssueUpdate(
  $id: String!,
  $title: String!,
  $description: String
) {
  issueUpdate(
    id: $id,
    input: {
      title: $title,
      description: $description
    }
  ) {
    success
    issue {
      id
      identifier
      title
    }
  }
}
"""


FIND_ISSUE_BY_MARKER = """
query WorkflowIssueByMarker(
  $marker: String!
) {
  issues(
    first: 2,
    filter: {
      description: {
        contains: $marker
      }
    }
  ) {
    nodes {
      id
      identifier
      title
      description
    }
  }
}
"""


def linear_task_marker(
    task_id: str,
) -> str:
    return (
        "workflow-engine-task:"
        f"{task_id}"
    )


def task_description(
    *,
    task_id: str,
    version: int,
    project: str,
    priority: str,
    due_date: str | None,
) -> str:
    due = due_date or "none"
    marker = linear_task_marker(
        task_id
    )

    return (
        "Created by workflow-engine from validated canonical state.\n\n"
        f"- Task ID: `{task_id}`\n"
        f"- Idempotency marker: `{marker}`\n"
        f"- Version: `{version}`\n"
        f"- Project: `{project}`\n"
        f"- Priority: `{priority}`\n"
        f"- Due date: `{due}`\n"
    )


async def seed_deliveries(db: aiosqlite.Connection) -> None:
    await db.execute(
        """
        INSERT OR IGNORE INTO deliveries (
            event_id,
            destination,
            status,
            last_synced_version
        )
        SELECT
            id,
            'markdown',
            CASE
                WHEN status = 'done' THEN 'delivered'
                ELSE 'pending'
            END,
            json_extract(payload_json, '$.version')
        FROM outbox
        WHERE event_type = 'TASK_CHANGED'
        """
    )

    await db.execute(
        """
        INSERT OR IGNORE INTO deliveries (
            event_id,
            destination,
            status,
            last_synced_version
        )
        SELECT
            id,
            'linear',
            CASE
                WHEN ? = 'live' THEN 'pending'
                ELSE 'dry_run'
            END,
            json_extract(payload_json, '$.version')
        FROM outbox
        WHERE event_type = 'TASK_CHANGED'
        """,
        (LINEAR_MODE,),
    )

    await db.execute(
        """
        INSERT OR IGNORE INTO deliveries (
            event_id,
            destination,
            status,
            last_synced_version
        )
        SELECT
            o.id,
            'calendar',
            CASE
                WHEN t.due_date IS NULL
                    THEN 'skipped_no_due_date'
                ELSE 'dry_run'
            END,
            json_extract(o.payload_json, '$.version')
        FROM outbox o
        LEFT JOIN tasks t
            ON t.id = json_extract(
                o.payload_json,
                '$.task_id'
            )
        WHERE o.event_type = 'TASK_CHANGED'
        """
    )

    await db.commit()


async def _get_mapping(
    db: aiosqlite.Connection,
    task_id: str,
) -> tuple[str | None, int]:
    cur = await db.execute(
        """
        SELECT
            external_object_id,
            last_synced_version
        FROM destination_objects
        WHERE task_id = ?
          AND destination = 'linear'
        """,
        (task_id,),
    )

    row = await cur.fetchone()

    if row is None:
        return None, 0

    return row[0], int(row[1] or 0)


async def _save_mapping(
    db: aiosqlite.Connection,
    *,
    task_id: str,
    external_object_id: str,
    version: int,
) -> None:
    await db.execute(
        """
        INSERT INTO destination_objects (
            task_id,
            destination,
            external_object_id,
            last_synced_version,
            updated_at
        )
        VALUES (
            ?,
            'linear',
            ?,
            ?,
            CURRENT_TIMESTAMP
        )
        ON CONFLICT(task_id, destination)
        DO UPDATE SET
            external_object_id = excluded.external_object_id,
            last_synced_version = excluded.last_synced_version,
            updated_at = CURRENT_TIMESTAMP
        """,
        (
            task_id,
            external_object_id,
            version,
        ),
    )


async def _ensure_create_intent(
    db: aiosqlite.Connection,
    *,
    task_id: str,
    version: int,
) -> str:
    marker = linear_task_marker(
        task_id
    )

    await db.execute(
        """
        INSERT INTO delivery_intents (
            task_id,
            destination,
            idempotency_marker,
            status,
            last_synced_version,
            updated_at
        )
        VALUES (
            ?,
            'linear',
            ?,
            'creating',
            ?,
            CURRENT_TIMESTAMP
        )
        ON CONFLICT(task_id, destination)
        DO UPDATE SET
            idempotency_marker = excluded.idempotency_marker,
            status = CASE
                WHEN delivery_intents.external_object_id IS NULL
                    THEN 'creating'
                ELSE delivery_intents.status
            END,
            last_synced_version = MAX(
                delivery_intents.last_synced_version,
                excluded.last_synced_version
            ),
            updated_at = CURRENT_TIMESTAMP
        """,
        (
            task_id,
            marker,
            version,
        ),
    )

    await db.commit()
    return marker


async def _resolve_create_intent(
    db: aiosqlite.Connection,
    *,
    task_id: str,
    external_object_id: str,
    version: int,
) -> None:
    await db.execute(
        """
        UPDATE delivery_intents
        SET
            status = 'resolved',
            external_object_id = ?,
            last_synced_version = MAX(
                last_synced_version,
                ?
            ),
            last_error = NULL,
            updated_at = CURRENT_TIMESTAMP
        WHERE task_id = ?
          AND destination = 'linear'
        """,
        (
            external_object_id,
            version,
            task_id,
        ),
    )


async def _record_intent_error(
    db: aiosqlite.Connection,
    *,
    task_id: str,
    error: str,
) -> None:
    await db.execute(
        """
        UPDATE delivery_intents
        SET
            last_error = ?,
            updated_at = CURRENT_TIMESTAMP
        WHERE task_id = ?
          AND destination = 'linear'
        """,
        (
            error[:500],
            task_id,
        ),
    )


async def _find_issue_by_marker(
    marker: str,
) -> dict | None:
    data = await linear_request(
        FIND_ISSUE_BY_MARKER,
        {
            "marker": marker,
        },
    )

    nodes = (
        (data.get("issues") or {})
        .get("nodes")
        or []
    )

    matches = [
        node
        for node in nodes
        if marker in (
            node.get("description")
            or ""
        )
    ]

    if len(matches) > 1:
        raise LinearError(
            "multiple Linear issues match "
            "the workflow idempotency marker"
        )

    return matches[0] if matches else None


async def _mark_delivery(
    db: aiosqlite.Connection,
    *,
    event_id: str,
    status: str,
    version: int,
    external_object_id: str | None = None,
    error: str | None = None,
    increment_attempt: bool = False,
) -> None:
    await db.execute(
        """
        UPDATE deliveries
        SET
            status = ?,
            attempts = attempts + ?,
            external_object_id = COALESCE(?, external_object_id),
            last_synced_version = ?,
            last_error = ?,
            updated_at = CURRENT_TIMESTAMP
        WHERE event_id = ?
          AND destination = 'linear'
        """,
        (
            status,
            1 if increment_attempt else 0,
            external_object_id,
            version,
            error,
            event_id,
        ),
    )


async def sync_linear_dryrun(
    db: aiosqlite.Connection,
) -> None:
    cur = await db.execute(
        """
        SELECT
            o.id,
            json_extract(o.payload_json, '$.task_id') AS task_id,
            COALESCE(
                json_extract(o.payload_json, '$.version'),
                1
            ) AS version
        FROM outbox o
        JOIN deliveries d
            ON d.event_id = o.id
           AND d.destination = 'linear'
        WHERE o.event_type = 'TASK_CHANGED'
        ORDER BY version, o.created_at
        """
    )

    rows = await cur.fetchall()

    for event_id, task_id, version in rows:
        if not task_id:
            continue

        version = int(version)
        external_id, last_version = await _get_mapping(
            db,
            task_id,
        )

        if (
            external_id
            and not external_id.startswith("dryrun-linear:")
        ):
            continue

        if external_id is None:
            external_id = f"dryrun-linear:{task_id}"

        if version > last_version:
            await _save_mapping(
                db,
                task_id=task_id,
                external_object_id=external_id,
                version=version,
            )

            action = (
                "CREATE"
                if last_version == 0
                else "UPDATE"
            )

            log.info(
                "linear dry-run %s task=%s %s->%s external=%s",
                action,
                task_id,
                last_version,
                version,
                external_id,
            )

        await _mark_delivery(
            db,
            event_id=event_id,
            status="dry_run",
            version=version,
            external_object_id=external_id,
        )

    await db.commit()


async def sync_linear_live(
    db: aiosqlite.Connection,
) -> None:
    if not LINEAR_API_KEY:
        log.warning(
            "Linear live mode blocked: LINEAR_API_KEY missing"
        )
        return

    if not LINEAR_TEAM_ID:
        log.warning(
            "Linear live mode blocked: LINEAR_TEAM_ID missing"
        )
        return

    cur = await db.execute(
        """
        SELECT
            o.id,
            json_extract(o.payload_json, '$.task_id') AS task_id,
            COALESCE(
                json_extract(o.payload_json, '$.version'),
                1
            ) AS version,
            t.title,
            t.project,
            t.priority,
            t.due_date
        FROM outbox o
        JOIN deliveries d
            ON d.event_id = o.id
           AND d.destination = 'linear'
        JOIN tasks t
            ON t.id = json_extract(
                o.payload_json,
                '$.task_id'
            )
        WHERE o.event_type = 'TASK_CHANGED'
        ORDER BY version, o.created_at
        """
    )

    rows = await cur.fetchall()

    for (
        event_id,
        task_id,
        version,
        title,
        project,
        priority,
        due_date,
    ) in rows:
        version = int(version)
        external_id, last_version = await _get_mapping(
            db,
            task_id,
        )

        is_dryrun_mapping = bool(
            external_id
            and external_id.startswith("dryrun-linear:")
        )

        if (
            external_id
            and not is_dryrun_mapping
            and version <= last_version
        ):
            await _mark_delivery(
                db,
                event_id=event_id,
                status="delivered",
                version=version,
                external_object_id=external_id,
            )
            continue

        description = task_description(
            task_id=task_id,
            version=version,
            project=project,
            priority=priority,
            due_date=due_date,
        )

        create_path = (
            external_id is None
            or is_dryrun_mapping
        )

        try:
            if create_path:
                marker = await _ensure_create_intent(
                    db,
                    task_id=task_id,
                    version=version,
                )

                recovered = (
                    await _find_issue_by_marker(
                        marker
                    )
                )

                if recovered is not None:
                    external_id = recovered["id"]

                    log.info(
                        "linear RECONCILE task=%s version=%s issue=%s",
                        task_id,
                        version,
                        (
                            recovered.get(
                                "identifier"
                            )
                            or external_id
                        ),
                    )

                else:
                    data = await linear_request(
                        CREATE_ISSUE,
                        {
                            "teamId": LINEAR_TEAM_ID,
                            "title": title,
                            "description": description,
                        },
                    )

                    result = (
                        data.get(
                            "issueCreate"
                        )
                        or {}
                    )
                    issue = (
                        result.get("issue")
                        or {}
                    )

                    if (
                        not result.get("success")
                        or not issue.get("id")
                    ):
                        raise LinearError(
                            "issueCreate returned no issue id"
                        )

                    external_id = issue["id"]

                    log.info(
                        "linear CREATE task=%s version=%s issue=%s",
                        task_id,
                        version,
                        (
                            issue.get(
                                "identifier"
                            )
                            or external_id
                        ),
                    )

            else:
                data = await linear_request(
                    UPDATE_ISSUE,
                    {
                        "id": external_id,
                        "title": title,
                        "description": description,
                    },
                )

                result = (
                    data.get(
                        "issueUpdate"
                    )
                    or {}
                )
                issue = (
                    result.get("issue")
                    or {}
                )

                if not result.get("success"):
                    raise LinearError(
                        "issueUpdate returned success=false"
                    )

                log.info(
                    "linear UPDATE task=%s %s->%s issue=%s",
                    task_id,
                    last_version,
                    version,
                    (
                        issue.get("identifier")
                        or external_id
                    ),
                )

            await _save_mapping(
                db,
                task_id=task_id,
                external_object_id=external_id,
                version=version,
            )

            if create_path:
                await _resolve_create_intent(
                    db,
                    task_id=task_id,
                    external_object_id=external_id,
                    version=version,
                )

            await _mark_delivery(
                db,
                event_id=event_id,
                status="delivered",
                version=version,
                external_object_id=external_id,
                error=None,
            )

            await db.commit()

        except LinearRegionBlocked as exc:
            if create_path:
                await _record_intent_error(
                    db,
                    task_id=task_id,
                    error=str(exc),
                )

            await _mark_delivery(
                db,
                event_id=event_id,
                status="blocked_region",
                version=version,
                error=str(exc)[:1000],
                increment_attempt=False,
            )

            await db.commit()

            log.error(
                "linear delivery blocked by region event=%s task=%s: %s",
                event_id,
                task_id,
                exc,
            )

        except Exception as exc:
            if create_path:
                await _record_intent_error(
                    db,
                    task_id=task_id,
                    error=str(exc),
                )

            await _mark_delivery(
                db,
                event_id=event_id,
                status="retry",
                version=version,
                error=str(exc)[:1000],
                increment_attempt=True,
            )

            await db.commit()

            log.exception(
                "linear delivery failed event=%s task=%s",
                event_id,
                task_id,
            )


async def report(
    db: aiosqlite.Connection,
) -> None:
    cur = await db.execute(
        """
        SELECT destination, status, COUNT(*)
        FROM deliveries
        GROUP BY destination, status
        ORDER BY destination, status
        """
    )

    rows = await cur.fetchall()

    if rows:
        log.info(
            "delivery state %s",
            ", ".join(
                f"{destination}:{status}={count}"
                for destination, status, count in rows
            ),
        )


async def run_cycle(
    db: aiosqlite.Connection,
) -> None:
    await seed_deliveries(db)

    if LINEAR_MODE == "live":
        await sync_linear_live(db)
    else:
        await sync_linear_dryrun(db)

    await report(db)


async def main() -> None:
    if LINEAR_MODE not in {"dry_run", "live"}:
        raise RuntimeError(
            "LINEAR_MODE must be dry_run or live"
        )

    db = await aiosqlite.connect(DB_PATH)

    try:
        await db.execute("PRAGMA busy_timeout=5000")
        await db.executescript(SCHEMA)
        await db.commit()

        log.info(
            "delivery worker started linear_mode=%s team=%s key=%s",
            LINEAR_MODE,
            LINEAR_TEAM_ID or "missing",
            "present" if LINEAR_API_KEY else "missing",
        )

        while True:
            try:
                await run_cycle(db)

            except asyncio.CancelledError:
                raise

            except Exception:
                log.exception("delivery cycle failed")

            await asyncio.sleep(POLL_SECONDS)

    finally:
        await db.close()


if __name__ == "__main__":
    asyncio.run(main())
