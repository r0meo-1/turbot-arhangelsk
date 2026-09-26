import argparse
import asyncio
import json
import logging
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from .db import Repository
from .gmail_source import GmailSource
from .models import EmailMessage
from .projection import MarkdownProjection
from .service import Engine, OutboxWorker


logging.basicConfig(
    level=os.getenv(
        "LOG_LEVEL",
        "INFO",
    ),
    format=(
        "%(asctime)s "
        "%(levelname)s "
        "%(name)s "
        "%(message)s"
    ),
)

log = logging.getLogger(
    "workflow-engine"
)


def settings():
    return {
        "db": os.getenv(
            "WORKFLOW_DB",
            "/var/lib/workflow-engine/workflow.db",
        ),
        "board": os.getenv(
            "WORKFLOW_BOARD",
            "/var/lib/workflow-engine/PROJECT_BOARD.md",
        ),
        "source": os.getenv(
            "SOURCE_MODE",
            "auto",
        ).lower(),
        "token": os.getenv(
            "GMAIL_TOKEN_FILE",
            "/etc/workflow-engine/token.json",
        ),
        "query": os.getenv(
            "GMAIL_QUERY",
            "newer_than:2d",
        ),
        "poll": int(
            os.getenv(
                "POLL_INTERVAL_SECONDS",
                "30",
            )
        ),
    }


async def source_cycle(
    source,
    engine,
    repo,
):
    checkpoint = await repo.gmail_history_id()

    batch = await source.fetch(
        start_history_id=checkpoint
    )

    for message in batch.messages:
        await engine.process(
            message
        )

    advanced = False

    if batch.next_history_id:
        advanced = (
            await repo.advance_gmail_history_id(
                batch.next_history_id
            )
        )

    return {
        "mode": batch.mode,
        "messages": len(batch.messages),
        "checkpoint_advanced": advanced,
    }


async def source_loop(
    source,
    engine,
    repo,
    interval,
):
    while True:
        try:
            result = await source_cycle(
                source,
                engine,
                repo,
            )

            log.info(
                "Gmail sync mode=%s "
                "messages=%d "
                "checkpoint_advanced=%s",
                result["mode"],
                result["messages"],
                result[
                    "checkpoint_advanced"
                ],
            )

        except asyncio.CancelledError:
            raise

        except Exception:
            log.exception(
                "source cycle failed"
            )

        await asyncio.sleep(interval)


async def idle_loop():
    log.warning(
        "Gmail ingestion disabled: "
        "token not installed yet"
    )

    while True:
        await asyncio.sleep(3600)


async def run_service():
    cfg = settings()

    repo = Repository(cfg["db"])
    await repo.connect()

    projection = MarkdownProjection(
        cfg["board"]
    )

    engine = Engine(repo)

    outbox = OutboxWorker(
        repo,
        projection,
    )

    token_exists = Path(
        cfg["token"]
    ).exists()

    if cfg["source"] == "gmail":
        if not token_exists:
            raise RuntimeError(
                f"Gmail token missing: {cfg['token']}"
            )

        producer = source_loop(
            GmailSource(
                cfg["token"],
                cfg["query"],
            ),
            engine,
            repo,
            cfg["poll"],
        )

    elif (
        cfg["source"] == "auto"
        and token_exists
    ):
        log.info(
            "Gmail token detected; "
            "enabling Gmail ingestion"
        )

        producer = source_loop(
            GmailSource(
                cfg["token"],
                cfg["query"],
            ),
            engine,
            repo,
            cfg["poll"],
        )

    else:
        producer = idle_loop()

    await asyncio.gather(
        producer,
        outbox.run(),
    )


async def show_status():
    cfg = settings()

    repo = Repository(cfg["db"])
    await repo.connect()

    try:
        result = await repo.counts()
        history_id = (
            await repo.gmail_history_id()
        )

        result.update({
            "db": cfg["db"],
            "board": cfg["board"],
            "source_mode": cfg["source"],
            "gmail_token_present": Path(
                cfg["token"]
            ).exists(),
            "gmail_checkpoint_present": (
                history_id is not None
            ),
        })

        print(
            json.dumps(
                result,
                indent=2,
                ensure_ascii=False,
            )
        )
    finally:
        await repo.close()


async def selftest():
    with tempfile.TemporaryDirectory() as td:
        db = f"{td}/test.db"
        board = f"{td}/board.md"

        repo = Repository(db)
        await repo.connect()

        engine = Engine(repo)

        worker = OutboxWorker(
            repo,
            MarkdownProjection(board),
        )

        first = EmailMessage(
            source="test",
            external_id="msg-1",
            thread_id="thread-1",
            sender="manager@example.test",
            subject="Deploy API",
            body=(
                "Please deploy API "
                "by 2026-09-27"
            ),
            received_at=datetime.now(
                timezone.utc
            ),
        )

        conflict = EmailMessage(
            source="test",
            external_id="msg-2",
            thread_id="thread-1",
            sender="manager@example.test",
            subject="Deploy API",
            body=(
                "Please deploy API "
                "by 2026-09-29"
            ),
            received_at=datetime.now(
                timezone.utc
            ),
        )

        assert await engine.process(first)
        assert not await engine.process(first)
        assert await engine.process(conflict)

        assert (
            await repo.advance_gmail_history_id(
                "100"
            )
        )
        assert not (
            await repo.advance_gmail_history_id(
                "99"
            )
        )

        await worker.drain()

        counts = await repo.counts()

        assert counts["messages"] == 2
        assert counts["gmail_mailbox"] == 1
        assert counts["tasks"] == 1
        assert counts["review_queue"] == 1

        text = Path(board).read_text(
            encoding="utf-8"
        )

        assert "Deploy API" in text
        assert "deadline conflict" in text

        print("SELFTEST PASS")
        print(
            json.dumps(
                counts,
                indent=2,
            )
        )


def main():
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "command",
        choices=[
            "run",
            "status",
            "selftest",
        ],
    )

    args = parser.parse_args()

    if args.command == "run":
        asyncio.run(run_service())

    elif args.command == "status":
        asyncio.run(show_status())

    elif args.command == "selftest":
        asyncio.run(selftest())


if __name__ == "__main__":
    main()
