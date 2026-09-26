"""Ensure the VK autopost ledger releases SQLite handles safely."""

from contextlib import closing
from datetime import datetime, timezone
import sqlite3
from unittest.mock import Mock

import pytest

import vk_autopost as ap


def test_lookup_closes_its_database(tmp_path, monkeypatch):
    monkeypatch.setattr(ap, "DEFAULT_DB", tmp_path / "lookup.sqlite")
    with closing(ap._db()) as connection:
        monkeypatch.setattr(ap, "_db", lambda: connection)
        assert ap.already_published("test", "test", "2026-W39") is False
        with pytest.raises(sqlite3.ProgrammingError, match="closed"):
            connection.execute("SELECT 1")


def test_mark_commits_before_closing(tmp_path, monkeypatch):
    path = tmp_path / "mark.sqlite"
    monkeypatch.setattr(ap, "DEFAULT_DB", path)
    now = datetime(2026, 9, 24, tzinfo=timezone.utc)
    with closing(ap._db()) as connection:
        monkeypatch.setattr(ap, "_db", lambda: connection)
        ap.mark_published("test", "test", "2026-W39", 42, now)
        with pytest.raises(sqlite3.ProgrammingError, match="closed"):
            connection.execute("SELECT 1")
    with closing(sqlite3.connect(path)) as reopened:
        row = reopened.execute(
            "SELECT post_id, published_at FROM vk_autopost_log"
        ).fetchone()
        assert row == (42, now.isoformat())


def test_mark_failure_closes_and_rolls_back(tmp_path, monkeypatch):
    path = tmp_path / "failure.sqlite"
    monkeypatch.setattr(ap, "DEFAULT_DB", path)
    with closing(ap._db()) as connection:
        connection.execute(
            "CREATE TRIGGER reject_write BEFORE INSERT ON vk_autopost_log "
            "BEGIN SELECT RAISE(ABORT, 'synthetic write failure'); END"
        )
        connection.commit()
        monkeypatch.setattr(ap, "_db", lambda: connection)
        with pytest.raises(sqlite3.IntegrityError, match="synthetic write failure"):
            ap.mark_published(
                "test", "test", "2026-W39", 42, datetime(2026, 9, 24)
            )
        with pytest.raises(sqlite3.ProgrammingError, match="closed"):
            connection.execute("SELECT 1")
    with closing(sqlite3.connect(path)) as reopened:
        assert reopened.execute(
            "SELECT COUNT(*) FROM vk_autopost_log"
        ).fetchone()[0] == 0


def test_schema_failure_closes_connection(monkeypatch):
    connection = Mock()
    connection.execute.side_effect = sqlite3.OperationalError(
        "synthetic schema failure"
    )
    monkeypatch.setattr(ap.sqlite3, "connect", lambda *_args, **_kwargs: connection)
    with pytest.raises(sqlite3.OperationalError, match="synthetic schema failure"):
        ap._db()
    connection.close.assert_called_once_with()
