from pathlib import Path

import pytest

from workflow_engine.db import Repository, SCHEMA
from workflow_engine.gmail_source import (
    GmailBatch,
    GmailSource,
    GmailWatch,
)
from workflow_engine.main import (
    ensure_watch_cycle,
    notification_cycle,
    settings,
    source_cycle,
)


ROOT = Path(__file__).resolve().parents[1]


class _Request:
    def __init__(self, payload):
        self.payload = payload

    def execute(self):
        return self.payload


class _FakeHistory:
    def __init__(self):
        self.calls = []

    def list(self, **kwargs):
        self.calls.append(kwargs)

        if kwargs.get("pageToken") == "next":
            return _Request({
                "historyId": "109",
                "history": [
                    {
                        "messagesAdded": [
                            {
                                "message": {
                                    "id": "m2",
                                }
                            }
                        ]
                    }
                ],
            })

        return _Request({
            "historyId": "105",
            "nextPageToken": "next",
            "history": [
                {
                    "messagesAdded": [
                        {
                            "message": {
                                "id": "m1",
                            }
                        },
                        {
                            "message": {
                                "id": "m1",
                            }
                        },
                    ]
                }
            ],
        })


class _FakeMessages:
    def __init__(self):
        self.list_calls = []

    def list(self, **kwargs):
        self.list_calls.append(kwargs)

        if kwargs.get("pageToken") == "next":
            return _Request({
                "messages": [
                    {"id": "m2"},
                ]
            })

        return _Request({
            "messages": [
                {"id": "m1"},
            ],
            "nextPageToken": "next",
        })

    def get(self, **kwargs):
        message_id = kwargs["id"]

        return _Request({
            "id": message_id,
            "threadId": f"thread-{message_id}",
            "internalDate": "0",
            "payload": {
                "headers": [
                    {
                        "name": "From",
                        "value": "manager@example.test",
                    },
                    {
                        "name": "Subject",
                        "value": message_id,
                    },
                ],
                "body": {},
            },
        })


class _FakeUsers:
    def __init__(self):
        self.history_api = _FakeHistory()
        self.messages_api = _FakeMessages()
        self.watch_calls = []

    def history(self):
        return self.history_api

    def messages(self):
        return self.messages_api

    def watch(self, **kwargs):
        self.watch_calls.append(
            kwargs
        )
        return _Request({
            "historyId": "300",
            "expiration": "9999999999999",
        })

    def getProfile(self, **kwargs):
        assert kwargs == {"userId": "me"}
        return _Request({
            "historyId": "200",
        })


class _FakeService:
    def __init__(self):
        self.users_api = _FakeUsers()

    def users(self):
        return self.users_api


def _source_with_fake_service():
    source = GmailSource.__new__(
        GmailSource
    )
    source.service = _FakeService()
    source.query = "newer_than:2d"
    return source


def test_runtime_defaults_are_explicit(monkeypatch):
    for name in (
        "WORKFLOW_DB",
        "WORKFLOW_BOARD",
        "SOURCE_MODE",
        "GMAIL_TOKEN_FILE",
        "GMAIL_QUERY",
        "POLL_INTERVAL_SECONDS",
        "GMAIL_INGEST_MODE",
        "GMAIL_NOTIFICATION_POLL_SECONDS",
        "GMAIL_PUBSUB_TOPIC",
        "GMAIL_PUBSUB_BIND",
        "GMAIL_PUBSUB_PORT",
        "GMAIL_PUBSUB_PATH",
        "GMAIL_PUBSUB_AUDIENCE",
        "GMAIL_PUBSUB_SERVICE_ACCOUNT",
        "GMAIL_WATCH_CHECK_SECONDS",
        "GMAIL_WATCH_RENEW_BEFORE_SECONDS",
    ):
        monkeypatch.delenv(name, raising=False)

    cfg = settings()
    assert cfg["source"] == "auto"
    assert cfg["gmail_ingest"] == "poll"
    assert cfg["query"] == "newer_than:2d"
    assert cfg["poll"] == 30
    assert cfg["notification_poll"] == 1
    assert cfg["pubsub_bind"] == "127.0.0.1"
    assert cfg["pubsub_port"] == 8091
    assert cfg["pubsub_path"] == "/gmail/pubsub"
    assert cfg["watch_check"] == 3600
    assert cfg["watch_renew_before"] == 86400
    assert cfg["token"] == "/etc/workflow-engine/token.json"


def test_gmail_users_watch_uses_topic_and_validates_result():
    source = _source_with_fake_service()

    watch = source._start_watch_sync(
        "projects/test/topics/gmail"
    )

    assert watch == GmailWatch(
        history_id="300",
        expiration_ms=9999999999999,
    )
    calls = (
        source.service.users_api
        .watch_calls
    )
    assert calls == [{
        "userId": "me",
        "body": {
            "topicName": (
                "projects/test/topics/gmail"
            ),
        },
    }]


def test_gmail_history_sync_pages_and_deduplicates():
    source = _source_with_fake_service()

    batch = source._history_sync(
        "100",
        50,
    )

    assert batch.mode == "history"
    assert batch.next_history_id == "109"
    assert [
        message.external_id
        for message in batch.messages
    ] == ["m1", "m2"]

    calls = (
        source.service.users_api
        .history_api.calls
    )
    assert calls[0]["startHistoryId"] == "100"
    assert "pageToken" not in calls[0]
    assert calls[1]["pageToken"] == "next"


def test_gmail_bootstrap_pages_without_skipping_gap():
    source = _source_with_fake_service()

    batch = source._bootstrap_sync(
        50,
        mode="bootstrap",
    )

    assert batch.mode == "bootstrap"
    assert batch.next_history_id == "200"
    assert [
        message.external_id
        for message in batch.messages
    ] == ["m1", "m2"]

    calls = (
        source.service.users_api
        .messages_api.list_calls
    )
    assert "pageToken" not in calls[0]
    assert calls[1]["pageToken"] == "next"


@pytest.mark.asyncio
async def test_gmail_checkpoint_is_monotonic(tmp_path):
    repo = Repository(
        str(tmp_path / "workflow.db")
    )
    await repo.connect()

    try:
        assert (
            await repo.gmail_history_id()
            is None
        )
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
        assert not (
            await repo.advance_gmail_history_id(
                "100"
            )
        )
        assert (
            await repo.advance_gmail_history_id(
                "101"
            )
        )
        assert (
            await repo.gmail_history_id()
            == "101"
        )

        await repo.record_gmail_watch(
            topic_name=(
                "projects/test/topics/gmail"
            ),
            history_id="101",
            expiration_ms=9999999999999,
        )
        watch = (
            await repo.gmail_watch_state()
        )
        assert watch["history_id"] == "101"

        assert (
            await repo.record_gmail_notification(
                pubsub_message_id="m-1",
                history_id="102",
            )
        )
        assert not (
            await repo.record_gmail_notification(
                pubsub_message_id="m-2",
                history_id="102",
            )
        )

        pending = (
            await repo.pending_gmail_notification()
        )
        assert pending[
            "history_id"
        ] == "102"

        assert (
            await repo.mark_gmail_notifications_through(
                "102"
            )
            == 1
        )
        assert (
            await repo.pending_gmail_notification()
            is None
        )
    finally:
        await repo.close()


@pytest.mark.asyncio
async def test_watch_cycle_persists_state_and_enqueues_catchup():
    class RepoStub:
        def __init__(self):
            self.watch = None
            self.notifications = []

        async def gmail_watch_state(self):
            return self.watch

        async def record_gmail_watch(
            self,
            **kwargs,
        ):
            self.watch = kwargs

        async def record_gmail_notification(
            self,
            **kwargs,
        ):
            self.notifications.append(
                kwargs
            )
            return True

    class SourceStub:
        def __init__(self):
            self.calls = []

        async def start_watch(
            self,
            topic_name,
        ):
            self.calls.append(
                topic_name
            )
            return GmailWatch(
                history_id="500",
                expiration_ms=200000,
            )

    cfg = {
        "pubsub_topic": (
            "projects/test/topics/gmail"
        ),
        "watch_renew_before": 60,
    }
    repo = RepoStub()
    source = SourceStub()

    assert await ensure_watch_cycle(
        source,
        repo,
        cfg,
        now_ms=100000,
    )

    assert source.calls == [
        "projects/test/topics/gmail"
    ]
    assert repo.watch[
        "history_id"
    ] == "500"
    assert repo.notifications == [{
        "pubsub_message_id": (
            "watch:500:200000"
        ),
        "history_id": "500",
    }]

    repo.watch = {
        "topic_name": (
            "projects/test/topics/gmail"
        ),
        "expiration_ms": 300000,
    }

    assert not await ensure_watch_cycle(
        source,
        repo,
        cfg,
        now_ms=100000,
    )
    assert len(source.calls) == 1

    repo.watch = {
        "topic_name": (
            "projects/test/topics/old"
        ),
        "expiration_ms": 300000,
    }

    assert await ensure_watch_cycle(
        source,
        repo,
        cfg,
        now_ms=100000,
    )
    assert len(source.calls) == 2


@pytest.mark.asyncio
async def test_notification_cycle_marks_only_after_sync_success():
    class RepoStub:
        def __init__(self):
            self.checkpoint = "100"
            self.marked = []

        async def pending_gmail_notification(self):
            return {
                "history_id": "101",
            }

        async def gmail_history_id(self):
            return self.checkpoint

        async def advance_gmail_history_id(
            self,
            history_id,
        ):
            self.checkpoint = history_id
            return True

        async def mark_gmail_notifications_through(
            self,
            history_id,
        ):
            self.marked.append(
                history_id
            )
            return 1

    class SourceStub:
        async def fetch(
            self,
            start_history_id=None,
        ):
            assert start_history_id == "100"
            return GmailBatch(
                messages=["message"],
                next_history_id="101",
                mode="history",
            )

    class WorkingEngine:
        async def process(self, message):
            assert message == "message"
            return True

    class FailingEngine:
        async def process(self, message):
            raise RuntimeError("boom")

    repo = RepoStub()
    source = SourceStub()

    with pytest.raises(
        RuntimeError,
        match="boom",
    ):
        await notification_cycle(
            source,
            FailingEngine(),
            repo,
        )

    assert repo.marked == []
    assert repo.checkpoint == "100"

    result = await notification_cycle(
        source,
        WorkingEngine(),
        repo,
    )

    assert repo.checkpoint == "101"
    assert repo.marked == ["101"]
    assert result[
        "notifications_marked"
    ] == 1


@pytest.mark.asyncio
async def test_source_cycle_advances_only_after_processing():
    class RepoStub:
        def __init__(self):
            self.advanced = []

        async def gmail_history_id(self):
            return "100"

        async def advance_gmail_history_id(
            self,
            history_id,
        ):
            self.advanced.append(
                history_id
            )
            return True

    class SourceStub:
        async def fetch(
            self,
            start_history_id=None,
        ):
            assert start_history_id == "100"
            return GmailBatch(
                messages=["message"],
                next_history_id="101",
                mode="history",
            )

    class FailingEngine:
        async def process(self, message):
            raise RuntimeError("boom")

    class WorkingEngine:
        async def process(self, message):
            assert message == "message"
            return True

    repo = RepoStub()
    source = SourceStub()

    with pytest.raises(
        RuntimeError,
        match="boom",
    ):
        await source_cycle(
            source,
            FailingEngine(),
            repo,
        )

    assert repo.advanced == []

    result = await source_cycle(
        source,
        WorkingEngine(),
        repo,
    )

    assert repo.advanced == ["101"]
    assert result == {
        "mode": "history",
        "messages": 1,
        "checkpoint_advanced": True,
    }


def test_gmail_contract_uses_history_checkpoint_path():
    source = (
        ROOT
        / "workflow_engine"
        / "gmail_source.py"
    ).read_text(
        encoding="utf-8"
    )

    assert "gmail.readonly" in source
    assert ".getProfile(" in source
    assert ".history()" in source
    assert ".list(" in source
    assert ".get(" in source
    assert "startHistoryId" in source
    assert "nextPageToken" in source
    assert "messagesAdded" in source


def test_sqlite_contract_has_message_and_outbox_idempotency():
    db_source = (
        ROOT
        / "workflow_engine"
        / "db.py"
    ).read_text(
        encoding="utf-8"
    )

    assert "UNIQUE(source, external_id)" in SCHEMA
    assert "gmail_mailbox" in SCHEMA
    assert "gmail_watch" in SCHEMA
    assert "gmail_notification" in SCHEMA
    assert "history_id TEXT NOT NULL" in SCHEMA
    assert "pubsub_message_id TEXT NOT NULL UNIQUE" in SCHEMA
    assert "dedupe_key TEXT NOT NULL UNIQUE" in SCHEMA
    assert "event_key TEXT NOT NULL UNIQUE" in SCHEMA
    assert "BEGIN IMMEDIATE" in db_source
    assert "INSERT OR IGNORE INTO messages" in db_source
    assert "INSERT OR IGNORE INTO task_sources" in db_source
    assert "INSERT OR IGNORE INTO outbox" in db_source


def test_runtime_tree_contains_no_runtime_state_files():
    forbidden_names = {
        ".env",
        "token.json",
        "credentials.json",
        "workflow.db",
    }
    for path in ROOT.rglob("*"):
        if path.is_file():
            assert path.name not in forbidden_names
            assert path.suffix not in {".sqlite", ".db"}
            assert not path.name.endswith(".bak")
