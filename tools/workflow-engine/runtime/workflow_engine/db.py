import asyncio
import json
import uuid
from pathlib import Path

import aiosqlite

from .logic import priority_band, utcnow
from .models import EmailMessage, TaskCandidate


SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,
    external_id TEXT NOT NULL,
    thread_id TEXT,
    sender TEXT,
    subject TEXT,
    received_at TEXT NOT NULL,
    processed_at TEXT NOT NULL,
    UNIQUE(source, external_id)
);

CREATE TABLE IF NOT EXISTS gmail_mailbox (
    mailbox TEXT PRIMARY KEY,
    history_id TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY,
    dedupe_key TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL,
    project TEXT NOT NULL,
    priority_score INTEGER NOT NULL,
    priority TEXT NOT NULL,
    owner TEXT,
    due_date TEXT,
    status TEXT NOT NULL DEFAULT 'open',
    confidence REAL NOT NULL,
    version INTEGER NOT NULL DEFAULT 1,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS task_sources (
    task_id TEXT NOT NULL,
    source TEXT NOT NULL,
    external_id TEXT NOT NULL,
    UNIQUE(task_id, source, external_id)
);

CREATE TABLE IF NOT EXISTS review_queue (
    id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    external_id TEXT NOT NULL,
    candidate_json TEXT NOT NULL,
    reason TEXT NOT NULL,
    created_at TEXT NOT NULL,
    resolved INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS outbox (
    id TEXT PRIMARY KEY,
    event_key TEXT NOT NULL UNIQUE,
    event_type TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    attempts INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    last_error TEXT
);

CREATE INDEX IF NOT EXISTS idx_outbox_status
ON outbox(status, created_at);
"""


class Repository:
    def __init__(self, path: str):
        self.path = path
        self.db = None
        self.lock = asyncio.Lock()

    async def connect(self):
        Path(self.path).parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        self.db = await aiosqlite.connect(self.path)
        self.db.row_factory = aiosqlite.Row

        await self.db.execute(
            "PRAGMA journal_mode=WAL"
        )

        await self.db.executescript(SCHEMA)
        await self.db.commit()

    async def gmail_history_id(
        self,
        mailbox="me",
    ):
        cur = await self.db.execute(
            """
            SELECT history_id
            FROM gmail_mailbox
            WHERE mailbox = ?
            """,
            (mailbox,),
        )
        row = await cur.fetchone()
        return row["history_id"] if row else None

    async def advance_gmail_history_id(
        self,
        history_id,
        mailbox="me",
    ):
        value = str(history_id).strip()

        if not value.isdigit():
            raise ValueError(
                "Gmail history_id must be decimal"
            )

        async with self.lock:
            await self.db.execute(
                "BEGIN IMMEDIATE"
            )

            try:
                cur = await self.db.execute(
                    """
                    SELECT history_id
                    FROM gmail_mailbox
                    WHERE mailbox = ?
                    """,
                    (mailbox,),
                )
                row = await cur.fetchone()

                if (
                    row is not None
                    and int(value)
                    <= int(row["history_id"])
                ):
                    await self.db.rollback()
                    return False

                await self.db.execute(
                    """
                    INSERT INTO gmail_mailbox (
                        mailbox,
                        history_id,
                        updated_at
                    )
                    VALUES (?, ?, ?)
                    ON CONFLICT(mailbox)
                    DO UPDATE SET
                        history_id = excluded.history_id,
                        updated_at = excluded.updated_at
                    """,
                    (
                        mailbox,
                        value,
                        utcnow().isoformat(),
                    ),
                )

                await self.db.commit()
                return True

            except Exception:
                await self.db.rollback()
                raise

    async def ingest(
        self,
        message: EmailMessage,
        candidates: list[TaskCandidate],
        validator,
    ):
        async with self.lock:
            await self.db.execute("BEGIN IMMEDIATE")

            try:
                await self.db.execute(
                    """
                    INSERT OR IGNORE INTO messages (
                        source,
                        external_id,
                        thread_id,
                        sender,
                        subject,
                        received_at,
                        processed_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        message.source,
                        message.external_id,
                        message.thread_id,
                        message.sender,
                        message.subject,
                        message.received_at.isoformat(),
                        utcnow().isoformat(),
                    ),
                )

                cur = await self.db.execute(
                    "SELECT changes()"
                )
                changed = (await cur.fetchone())[0]

                if changed == 0:
                    await self.db.rollback()
                    return False

                for candidate in candidates:
                    await self._apply_candidate(
                        message,
                        candidate,
                        validator,
                    )

                await self.db.commit()
                return True

            except Exception:
                await self.db.rollback()
                raise

    async def _apply_candidate(
        self,
        message,
        candidate,
        validator,
    ):
        cur = await self.db.execute(
            """
            SELECT *
            FROM tasks
            WHERE dedupe_key = ?
            """,
            (candidate.dedupe_key,),
        )

        row = await cur.fetchone()
        existing = dict(row) if row else None

        decision = validator.validate(
            candidate,
            existing,
        )

        if decision.action == "REVIEW":
            await self.db.execute(
                """
                INSERT INTO review_queue (
                    id,
                    source,
                    external_id,
                    candidate_json,
                    reason,
                    created_at
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    str(uuid.uuid4()),
                    message.source,
                    message.external_id,
                    json.dumps(
                        candidate.__dict__
                        if hasattr(candidate, "__dict__")
                        else {
                            name: getattr(candidate, name)
                            for name in candidate.__slots__
                        }
                    ),
                    decision.reason,
                    utcnow().isoformat(),
                ),
            )
            return

        if existing:
            task_id = existing["id"]

            new_score = max(
                existing["priority_score"],
                candidate.priority_score,
            )

            version = existing["version"] + 1

            await self.db.execute(
                """
                UPDATE tasks
                SET
                    priority_score = ?,
                    priority = ?,
                    due_date = COALESCE(due_date, ?),
                    confidence = MAX(confidence, ?),
                    version = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    new_score,
                    priority_band(new_score),
                    candidate.due_date,
                    candidate.confidence,
                    version,
                    utcnow().isoformat(),
                    task_id,
                ),
            )

        else:
            task_id = str(uuid.uuid4())
            version = 1

            await self.db.execute(
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
                    confidence,
                    version,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    task_id,
                    candidate.dedupe_key,
                    candidate.title,
                    candidate.project,
                    candidate.priority_score,
                    candidate.priority,
                    candidate.owner,
                    candidate.due_date,
                    candidate.confidence,
                    version,
                    utcnow().isoformat(),
                ),
            )

        await self.db.execute(
            """
            INSERT OR IGNORE INTO task_sources (
                task_id,
                source,
                external_id
            )
            VALUES (?, ?, ?)
            """,
            (
                task_id,
                message.source,
                message.external_id,
            ),
        )

        event_key = f"task:{task_id}:v:{version}"

        await self.db.execute(
            """
            INSERT OR IGNORE INTO outbox (
                id,
                event_key,
                event_type,
                payload_json,
                created_at
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                str(uuid.uuid4()),
                event_key,
                "TASK_CHANGED",
                json.dumps({
                    "task_id": task_id,
                    "version": version,
                }),
                utcnow().isoformat(),
            ),
        )

    async def tasks(self):
        cur = await self.db.execute(
            """
            SELECT *
            FROM tasks
            ORDER BY
                priority_score DESC,
                due_date IS NULL,
                due_date,
                updated_at DESC
            """
        )
        return [
            dict(r)
            for r in await cur.fetchall()
        ]

    async def reviews(self):
        cur = await self.db.execute(
            """
            SELECT *
            FROM review_queue
            WHERE resolved = 0
            ORDER BY created_at
            """
        )
        return [
            dict(r)
            for r in await cur.fetchall()
        ]

    async def next_outbox(self):
        cur = await self.db.execute(
            """
            SELECT *
            FROM outbox
            WHERE status = 'pending'
            ORDER BY created_at
            LIMIT 1
            """
        )

        row = await cur.fetchone()
        return dict(row) if row else None

    async def mark_done(self, event_id):
        await self.db.execute(
            """
            UPDATE outbox
            SET status = 'done'
            WHERE id = ?
            """,
            (event_id,),
        )
        await self.db.commit()

    async def mark_failure(
        self,
        event_id,
        attempts,
        error,
    ):
        status = (
            "dead"
            if attempts >= 8
            else "pending"
        )

        await self.db.execute(
            """
            UPDATE outbox
            SET
                status = ?,
                attempts = ?,
                last_error = ?
            WHERE id = ?
            """,
            (
                status,
                attempts,
                str(error)[:1000],
                event_id,
            ),
        )

        await self.db.commit()

    async def close(self):
        if self.db is not None:
            await self.db.close()
            self.db = None

    async def counts(self):
        result = {}

        for table in (
            "messages",
            "gmail_mailbox",
            "tasks",
            "review_queue",
            "outbox",
        ):
            cur = await self.db.execute(
                f"SELECT COUNT(*) FROM {table}"
            )
            result[table] = (
                await cur.fetchone()
            )[0]

        cur = await self.db.execute(
            """
            SELECT COUNT(*)
            FROM outbox
            WHERE status = 'pending'
            """
        )

        result["outbox_pending"] = (
            await cur.fetchone()
        )[0]

        return result
