from pathlib import Path

import pytest

from workflow_engine.db import Repository, SCHEMA
from workflow_engine.gmail_source import GmailBatch, GmailSource
from workflow_engine.main import settings, source_cycle


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

    def history(self):
        return self.history_api

    def messages(self):
        return self.messages_api

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
    ):
        monkeypatch.delenv(name, raising=False)

    cfg = settings()
    assert cfg["source"] == "auto"
    assert cfg["query"] == "newer_than:2d"
    assert cfg["poll"] == 30
    assert cfg["token"] == "/etc/workflow-engine/token.json"


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
    finally:
        await repo.close()


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
    assert "history_id TEXT NOT NULL" in SCHEMA
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
