import argparse
import asyncio
import json
import logging
import os
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

from .db import Repository
from .gmail_pubsub import (
    GmailPubSubReceiver,
    GoogleOidcVerifier,
)
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
        "gmail_ingest": os.getenv(
            "GMAIL_INGEST_MODE",
            "poll",
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
        "notification_poll": float(
            os.getenv(
                "GMAIL_NOTIFICATION_POLL_SECONDS",
                "1",
            )
        ),
        "pubsub_topic": os.getenv(
            "GMAIL_PUBSUB_TOPIC",
            "",
        ).strip(),
        "pubsub_bind": os.getenv(
            "GMAIL_PUBSUB_BIND",
            "127.0.0.1",
        ).strip(),
        "pubsub_port": int(
            os.getenv(
                "GMAIL_PUBSUB_PORT",
                "8091",
            )
        ),
        "pubsub_path": os.getenv(
            "GMAIL_PUBSUB_PATH",
            "/gmail/pubsub",
        ).strip(),
        "pubsub_audience": os.getenv(
            "GMAIL_PUBSUB_AUDIENCE",
            "",
        ).strip(),
        "pubsub_service_account": os.getenv(
            "GMAIL_PUBSUB_SERVICE_ACCOUNT",
            "",
        ).strip(),
        "watch_check": int(
            os.getenv(
                "GMAIL_WATCH_CHECK_SECONDS",
                "3600",
            )
        ),
        "watch_renew_before": int(
            os.getenv(
                "GMAIL_WATCH_RENEW_BEFORE_SECONDS",
                "86400",
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


async def ensure_watch_cycle(
    source,
    repo,
    cfg,
    *,
    now_ms=None,
):
    if now_ms is None:
        now_ms = int(
            time.time() * 1000
        )

    state = await repo.gmail_watch_state()

    renew_before_ms = (
        int(cfg["watch_renew_before"])
        * 1000
    )

    if (
        state is not None
        and state["topic_name"]
        == cfg["pubsub_topic"]
        and int(
            state["expiration_ms"]
        ) - now_ms
        > renew_before_ms
    ):
        return False

    watch = await source.start_watch(
        cfg["pubsub_topic"]
    )

    await repo.record_gmail_watch(
        topic_name=cfg["pubsub_topic"],
        history_id=watch.history_id,
        expiration_ms=watch.expiration_ms,
    )

    await repo.record_gmail_notification(
        pubsub_message_id=(
            "watch:"
            f"{watch.history_id}:"
            f"{watch.expiration_ms}"
        ),
        history_id=watch.history_id,
    )

    return True


async def watch_loop(
    source,
    repo,
    cfg,
):
    while True:
        try:
            renewed = await ensure_watch_cycle(
                source,
                repo,
                cfg,
            )

            if renewed:
                log.info(
                    "Gmail watch created or renewed"
                )

        except asyncio.CancelledError:
            raise

        except Exception:
            log.exception(
                "Gmail watch renewal failed"
            )

        await asyncio.sleep(
            cfg["watch_check"]
        )


async def notification_cycle(
    source,
    engine,
    repo,
):
    pending = (
        await repo.pending_gmail_notification()
    )

    if pending is None:
        return None

    result = await source_cycle(
        source,
        engine,
        repo,
    )

    checkpoint = (
        await repo.gmail_history_id()
    )

    if checkpoint is None:
        raise RuntimeError(
            "Gmail checkpoint missing after sync"
        )

    result["notifications_marked"] = (
        await repo.mark_gmail_notifications_through(
            checkpoint
        )
    )

    return result


async def notification_loop(
    source,
    engine,
    repo,
    interval,
):
    while True:
        try:
            result = await notification_cycle(
                source,
                engine,
                repo,
            )

            if result is None:
                await asyncio.sleep(
                    interval
                )
                continue

            log.info(
                "Gmail notification sync "
                "mode=%s messages=%d "
                "notifications_marked=%d",
                result["mode"],
                result["messages"],
                result[
                    "notifications_marked"
                ],
            )

        except asyncio.CancelledError:
            raise

        except Exception:
            log.exception(
                "Gmail notification cycle failed"
            )
            await asyncio.sleep(
                interval
            )


def _require_watch_config(
    cfg,
):
    required = (
        "pubsub_topic",
        "pubsub_audience",
        "pubsub_service_account",
    )

    missing = [
        name
        for name in required
        if not cfg[name]
    ]

    if missing:
        raise RuntimeError(
            "Gmail watch mode missing configuration: "
            + ", ".join(missing)
        )


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

    gmail_enabled = (
        cfg["source"] == "gmail"
        or (
            cfg["source"] == "auto"
            and token_exists
        )
    )

    if (
        cfg["source"] == "gmail"
        and not token_exists
    ):
        await repo.close()
        raise RuntimeError(
            f"Gmail token missing: {cfg['token']}"
        )

    tasks = [
        outbox.run(),
    ]

    if gmail_enabled:
        source = GmailSource(
            cfg["token"],
            cfg["query"],
        )

        if cfg["gmail_ingest"] == "poll":
            tasks.append(
                source_loop(
                    source,
                    engine,
                    repo,
                    cfg["poll"],
                )
            )

        elif cfg["gmail_ingest"] == "watch":
            _require_watch_config(
                cfg
            )

            verifier = GoogleOidcVerifier(
                audience=(
                    cfg["pubsub_audience"]
                ),
                service_account=(
                    cfg[
                        "pubsub_service_account"
                    ]
                ),
            )

            receiver = GmailPubSubReceiver(
                repo=repo,
                verifier=verifier,
                host=cfg["pubsub_bind"],
                port=cfg["pubsub_port"],
                path=cfg["pubsub_path"],
            )

            tasks.extend([
                receiver.run(),
                watch_loop(
                    source,
                    repo,
                    cfg,
                ),
                notification_loop(
                    source,
                    engine,
                    repo,
                    cfg[
                        "notification_poll"
                    ],
                ),
            ])

        else:
            await repo.close()
            raise RuntimeError(
                "GMAIL_INGEST_MODE must be "
                "poll or watch"
            )

    else:
        tasks.append(
            idle_loop()
        )

    try:
        await asyncio.gather(
            *tasks
        )
    finally:
        await repo.close()


async def show_status():
    cfg = settings()

    repo = Repository(cfg["db"])
    await repo.connect()

    try:
        result = await repo.counts()
        history_id = (
            await repo.gmail_history_id()
        )
        watch = (
            await repo.gmail_watch_state()
        )

        result.update({
            "db": cfg["db"],
            "board": cfg["board"],
            "source_mode": cfg["source"],
            "gmail_ingest_mode": (
                cfg["gmail_ingest"]
            ),
            "gmail_token_present": Path(
                cfg["token"]
            ).exists(),
            "gmail_checkpoint_present": (
                history_id is not None
            ),
            "gmail_watch_present": (
                watch is not None
            ),
            "gmail_watch_expiration_ms": (
                watch["expiration_ms"]
                if watch
                else None
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

        await repo.record_gmail_watch(
            topic_name=(
                "projects/test/topics/gmail"
            ),
            history_id="100",
            expiration_ms=(
                9999999999999
            ),
        )

        assert (
            await repo.record_gmail_notification(
                pubsub_message_id="pubsub-1",
                history_id="100",
            )
        )
        assert not (
            await repo.record_gmail_notification(
                pubsub_message_id="pubsub-2",
                history_id="100",
            )
        )
        assert (
            await repo.mark_gmail_notifications_through(
                "100"
            )
            == 1
        )

        await worker.drain()

        counts = await repo.counts()

        assert counts["messages"] == 2
        assert counts["gmail_mailbox"] == 1
        assert counts["gmail_watch"] == 1
        assert counts[
            "gmail_notification"
        ] == 1
        assert counts[
            "gmail_notification_pending"
        ] == 0
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
