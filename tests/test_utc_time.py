"""Regression coverage for the legacy CRM naive-UTC contract."""
from __future__ import annotations

import ast
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from shared import utc_time
from shared.travel_crm import (
    BookingOutcome, LeadTimeline, ManagerTask, OutcomeStatus, Quote,
    TaskType, TripRequest, tasks_due_today, timeline_to_dict,
)
from shared import travel_crm_store as store

ROOT = Path(__file__).resolve().parents[1]


def test_clock_explicitly_requests_utc():
    moment = datetime(2026, 9, 24, 1, 2, 3, 456789, tzinfo=timezone.utc)
    with patch.object(utc_time, "datetime") as clock:
        clock.now.return_value = moment
        result = utc_time.utc_now_naive()
        clock.now.assert_called_once_with(timezone.utc)
    assert result == datetime(2026, 9, 24, 1, 2, 3, 456789)
    assert result.tzinfo is None


def test_default_factories_are_evaluated_per_instance():
    first = datetime(2026, 9, 24, 0, 0, tzinfo=timezone.utc)
    second = first + timedelta(seconds=1)
    third = first + timedelta(seconds=2)
    with patch.object(utc_time, "datetime") as clock:
        clock.now.side_effect = [first, second, third]
        q1 = Quote("q1", "r1", "Test Hotel")
        q2 = Quote("q2", "r1", "Test Hotel")
        outcome = BookingOutcome(OutcomeStatus.WON)
    assert q1.calculated_at == first.replace(tzinfo=None)
    assert q2.calculated_at == second.replace(tzinfo=None)
    assert outcome.decided_at == third.replace(tzinfo=None)
    assert clock.now.call_count == 3


def test_epoch_round_trip_preserves_naive_utc():
    value = datetime(2026, 9, 24, 1, 2, 3)
    expected = int(value.replace(tzinfo=timezone.utc).timestamp())
    assert store._to_epoch(value) == expected
    assert store._from_epoch(expected) == value
    assert store._from_epoch(expected).tzinfo is None
    shifted = value.replace(tzinfo=timezone.utc).astimezone(timezone(timedelta(hours=5, minutes=30)))
    assert store._to_epoch(shifted) == expected


def test_request_default_timestamp_uses_utc_clock():
    moment = datetime(2026, 9, 24, 1, 2, 3, tzinfo=timezone.utc)
    request = TripRequest("r1", "Test City", 2)
    with closing(sqlite3.connect(":memory:")) as conn:
        store.init_schema(conn.cursor())
        with patch.object(utc_time, "datetime") as clock:
            clock.now.return_value = moment
            store.upsert_request(conn, request)
        assert conn.execute("SELECT created_at, updated_at FROM crm_trip_requests WHERE request_id='r1'").fetchone() == (int(moment.timestamp()), int(moment.timestamp()))


def test_explicit_request_time_remains_authoritative():
    moment = datetime(2026, 9, 24, 4, 2, 3, tzinfo=timezone(timedelta(hours=3)))
    request = TripRequest("r1", "Test City", 2)
    with closing(sqlite3.connect(":memory:")) as conn:
        store.init_schema(conn.cursor())
        with patch.object(utc_time, "datetime") as clock:
            clock.now.side_effect = AssertionError("explicit time must not consult clock")
            store.upsert_request(conn, request, now=moment)
        assert conn.execute("SELECT created_at FROM crm_trip_requests WHERE request_id='r1'").fetchone()[0] == int(moment.timestamp())


def test_default_quote_keeps_legacy_json_shape():
    moment = datetime(2026, 9, 24, 1, 2, 3, tzinfo=timezone.utc)
    request = TripRequest("r1", "Test City", 2)
    with patch.object(utc_time, "datetime") as clock:
        clock.now.return_value = moment
        timeline = LeadTimeline(request).with_quote(Quote("q1", "r1", "Test Hotel"))
    assert timeline_to_dict(timeline)["quotes"][0]["calculated_at"] == "2026-09-24T01:02:03"


def test_task_ordering_keeps_legacy_naive_comparisons():
    moment = datetime(2026, 9, 24, 1, 2, 3, tzinfo=timezone.utc)
    with patch.object(utc_time, "datetime") as clock:
        clock.now.return_value = moment
        now = utc_time.utc_now_naive()
    due = ManagerTask("t1", "r1", TaskType.BUILD_SELECTION, now, now)
    future = ManagerTask("t2", "r1", TaskType.BUILD_SELECTION, now + timedelta(days=1), now)
    assert tasks_due_today((future, due), now) == [due]


def test_no_deprecated_factories_in_migrated_runtime_modules():
    for name in ("bot.py", "vk_bot.py", "website_app.py", "shared/travel_crm.py", "shared/travel_crm_store.py"):
        tree = ast.parse((ROOT / name).read_text(encoding="utf-8-sig"))
        deprecated = [n for n in ast.walk(tree) if isinstance(n, ast.Attribute)
                      and n.attr in {"utcnow", "utcfromtimestamp"}]
        assert not deprecated, name
