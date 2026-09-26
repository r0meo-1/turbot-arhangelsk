from pathlib import Path

from workflow_engine.main import settings
from workflow_engine.db import SCHEMA


ROOT = Path(__file__).resolve().parents[1]


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


def test_gmail_baseline_uses_readonly_list_and_get():
    source = (ROOT / "workflow_engine" / "gmail_source.py").read_text(
        encoding="utf-8"
    )
    assert "gmail.readonly" in source
    assert ".messages()" in source
    assert ".list(" in source
    assert ".get(" in source
    assert 'maxResults=limit' in source


def test_sqlite_contract_has_message_and_outbox_idempotency():
    db_source = (ROOT / "workflow_engine" / "db.py").read_text(
        encoding="utf-8"
    )

    assert "UNIQUE(source, external_id)" in SCHEMA
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
