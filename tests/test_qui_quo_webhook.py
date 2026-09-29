import json
import sqlite3
from contextlib import contextmanager

import pytest
from flask import Flask

from shared.qui_quo_webhook import (
    QuiQuoInbox,
    cleanup_before,
    create_blueprint,
    link_quote,
)


SECRET = "a" * 64


@pytest.fixture
def qq(tmp_path):
    db_path = tmp_path / "qq.sqlite"

    @contextmanager
    def db_cursor(commit=False):
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        try:
            cur = conn.cursor()
            yield cur
            if commit:
                conn.commit()
        finally:
            conn.close()

    with db_cursor(commit=True) as cur:
        cur.execute(
            """
            CREATE TABLE crm_activities (
                activity_id TEXT PRIMARY KEY,
                request_id TEXT NOT NULL,
                activity_type TEXT NOT NULL,
                summary TEXT,
                created_at INTEGER NOT NULL
            )
            """
        )

    app = Flask(__name__)
    app.register_blueprint(
        create_blueprint(
            db_cursor,
            secret_getter=lambda: SECRET,
            start_worker=False,
        )
    )
    return app.test_client(), db_cursor, QuiQuoInbox(db_cursor)


def payload(event="item_click"):
    item = {"pos": 4, "deposit_amount": 15000}
    data = {
        "event": event,
        "quote": {"id": "QQ-1234", "notes": "must not persist"},
        "client": {
            "id": 101,
            "name": "Synthetic Client",
            "phone": "+70000000000",
            "email": "synthetic@example.invalid",
        },
        "manager": {
            "id": 202,
            "name": "Synthetic Manager",
            "email": "manager@example.invalid",
        },
        "items": [item],
        "item_pos": 4,
        "unknown_future_field": {"anything": "ignored"},
    }
    if event not in {"item_click", "item_order", "item_deposit"}:
        data.pop("item_pos")
    return data


def test_test_request_returns_success_without_persistence(qq):
    client, db_cursor, _ = qq
    response = client.post(
        f"/qq-webhook/{SECRET}",
        json={"is_test": True},
    )
    assert response.status_code == 200
    assert response.get_json() == {"success": True}
    with db_cursor() as cur:
        assert cur.execute("SELECT COUNT(*) FROM qui_quo_events").fetchone()[0] == 0


def test_wrong_secret_is_rejected_without_persistence(qq):
    client, db_cursor, _ = qq
    response = client.post("/qq-webhook/not-the-secret", json=payload())
    assert response.status_code == 404
    with db_cursor() as cur:
        assert cur.execute("SELECT COUNT(*) FROM qui_quo_events").fetchone()[0] == 0


def test_json_event_is_durable_pii_minimized_and_idempotent(qq):
    client, db_cursor, _ = qq
    first = client.post(f"/qq-webhook/{SECRET}", json=payload())
    second = client.post(f"/qq-webhook/{SECRET}", json=payload())

    assert first.status_code == 200
    assert first.get_json() == {"success": True, "duplicate": False}
    assert second.status_code == 200
    assert second.get_json() == {"success": True, "duplicate": True}

    with db_cursor() as cur:
        rows = cur.execute(
            """
            SELECT event_type, quote_id, client_id, manager_id, item_pos,
                   deposit_amount, item_count, status
            FROM qui_quo_events
            """
        ).fetchall()

    assert len(rows) == 1
    assert tuple(rows[0]) == (
        "item_click",
        "QQ-1234",
        "101",
        "202",
        "4",
        "",
        1,
        "pending",
    )
    serialized = json.dumps([tuple(row) for row in rows])
    assert "+70000000000" not in serialized
    assert "synthetic@example.invalid" not in serialized
    assert "Synthetic Client" not in serialized


def test_legacy_form_json_transport_is_supported(qq):
    client, db_cursor, _ = qq
    response = client.post(
        f"/qq-webhook/{SECRET}",
        data={"json": json.dumps(payload("quote_open"))},
        content_type="application/x-www-form-urlencoded",
    )
    assert response.status_code == 200
    with db_cursor() as cur:
        row = cur.execute(
            "SELECT event_type, item_pos FROM qui_quo_events"
        ).fetchone()
    assert tuple(row) == ("quote_open", "")


def test_unknown_or_malformed_item_is_rejected_closed(qq):
    client, db_cursor, _ = qq
    broken = payload()
    broken["item_pos"] = 99
    response = client.post(f"/qq-webhook/{SECRET}", json=broken)
    assert response.status_code == 400
    assert response.get_json()["success"] is False
    with db_cursor() as cur:
        assert cur.execute("SELECT COUNT(*) FROM qui_quo_events").fetchone()[0] == 0


def test_oversized_payload_fails_closed(tmp_path):
    db_path = tmp_path / "small.sqlite"

    @contextmanager
    def db_cursor(commit=False):
        conn = sqlite3.connect(db_path)
        try:
            cur = conn.cursor()
            yield cur
            if commit:
                conn.commit()
        finally:
            conn.close()

    app = Flask(__name__)
    app.register_blueprint(
        create_blueprint(
            db_cursor,
            secret_getter=lambda: SECRET,
            max_body_bytes=1024,
            start_worker=False,
        )
    )
    response = app.test_client().post(
        f"/qq-webhook/{SECRET}",
        data=json.dumps({"padding": "x" * 5000}),
        content_type="application/json",
    )
    assert response.status_code == 413


def test_huge_decimal_exponent_is_validation_error_not_500(qq):
    client, db_cursor, _ = qq
    broken = payload("item_deposit")
    broken["items"][0]["deposit_amount"] = "1e1000000"

    response = client.post(f"/qq-webhook/{SECRET}", json=broken)

    assert response.status_code == 400
    assert response.get_json() == {
        "success": False,
        "error": "deposit_amount_invalid",
    }
    with db_cursor() as cur:
        assert cur.execute("SELECT COUNT(*) FROM qui_quo_events").fetchone()[0] == 0


def test_unlinked_event_waits_until_explicit_quote_mapping(qq):
    client, db_cursor, inbox = qq
    assert client.post(
        f"/qq-webhook/{SECRET}",
        json=payload("item_order"),
    ).status_code == 200

    assert inbox.process_pending_once(now=1000) == 0
    with db_cursor() as cur:
        event = cur.execute(
            "SELECT status, request_id, next_retry_at FROM qui_quo_events"
        ).fetchone()
    assert event[0] == "unmatched"
    assert event[1] is None
    assert event[2] >= 1060

    with db_cursor(commit=True) as cur:
        assert link_quote(
            cur.connection,
            "QQ-1234",
            "request-1",
            "main",
            now=1001,
        ) is True

    assert inbox.process_pending_once(now=1001) == 1
    with db_cursor() as cur:
        event = cur.execute(
            "SELECT status, request_id FROM qui_quo_events"
        ).fetchone()
        activity = cur.execute(
            "SELECT request_id, activity_type, summary FROM crm_activities"
        ).fetchone()
    assert tuple(event) == ("processed", "request-1")
    assert activity[0] == "request-1"
    assert activity[1] == "note"
    assert "item_order" in activity[2]


def test_event_projects_once_after_explicit_mapping(qq):
    client, db_cursor, inbox = qq
    with db_cursor(commit=True) as cur:
        link_quote(
            cur.connection,
            "QQ-1234",
            "request-1",
            "main",
            now=900,
        )

    assert client.post(
        f"/qq-webhook/{SECRET}",
        json=payload("item_order"),
    ).status_code == 200
    assert inbox.process_pending_once(now=1000) == 1

    assert client.post(
        f"/qq-webhook/{SECRET}",
        json=payload("item_order"),
    ).status_code == 200
    assert inbox.process_pending_once(now=1001) == 0

    with db_cursor() as cur:
        activities = cur.execute(
            "SELECT request_id, activity_type, summary FROM crm_activities"
        ).fetchall()
    assert len(activities) == 1


def test_projection_uses_receipt_time_not_retry_time(qq):
    client, db_cursor, inbox = qq
    with db_cursor(commit=True) as cur:
        link_quote(
            cur.connection,
            "QQ-1234",
            "request-1",
            "main",
            now=800,
        )

    assert client.post(
        f"/qq-webhook/{SECRET}",
        json=payload("quote_open"),
    ).status_code == 200
    with db_cursor(commit=True) as cur:
        cur.execute("UPDATE qui_quo_events SET received_at=777")

    assert inbox.process_pending_once(now=5000) == 1
    with db_cursor() as cur:
        created_at = cur.execute(
            "SELECT created_at FROM crm_activities"
        ).fetchone()[0]
    assert created_at == 777


def test_cross_store_projection_uses_explicit_projector(tmp_path):
    db_path = tmp_path / "cross.sqlite"

    @contextmanager
    def db_cursor(commit=False):
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        try:
            cur = conn.cursor()
            yield cur
            if commit:
                conn.commit()
        finally:
            conn.close()

    projected = []

    def projector(store, request_id, activity_id, summary, created_at):
        projected.append(
            (store, request_id, activity_id, summary, created_at)
        )

    inbox = QuiQuoInbox(db_cursor, projector=projector)
    inbox.init_schema()
    event = {
        "is_test": False,
        "event_key": "e" * 64,
        "event_type": "quote_open",
        "quote_id": "QQ-VK-1",
        "client_id": "10",
        "manager_id": "20",
        "item_pos": "",
        "deposit_amount": "",
        "item_count": 1,
    }
    assert inbox.enqueue(event) is True
    with db_cursor(commit=True) as cur:
        cur.execute("UPDATE qui_quo_events SET received_at=333")
        link_quote(
            cur.connection,
            "QQ-VK-1",
            "vk-lead-42",
            "vk",
            now=300,
        )

    assert inbox.process_pending_once(now=400) == 1
    assert projected[0][0:2] == ("vk", "vk-lead-42")
    assert projected[0][4] == 333


def test_deposit_amount_is_part_of_semantic_idempotency(qq):
    client, db_cursor, _ = qq
    first = payload("item_deposit")
    second = payload("item_deposit")
    second["items"][0]["deposit_amount"] = 20000

    assert client.post(f"/qq-webhook/{SECRET}", json=first).status_code == 200
    assert client.post(f"/qq-webhook/{SECRET}", json=second).status_code == 200

    with db_cursor() as cur:
        amounts = [
            row[0]
            for row in cur.execute(
                "SELECT deposit_amount FROM qui_quo_events ORDER BY deposit_amount"
            ).fetchall()
        ]
    assert amounts == ["15000", "20000"]


def test_processing_failure_retries_without_raw_payload(qq):
    client, db_cursor, inbox = qq
    with db_cursor(commit=True) as cur:
        link_quote(
            cur.connection,
            "QQ-1234",
            "request-2",
            "main",
            now=1900,
        )
        cur.execute("DROP TABLE crm_activities")

    assert client.post(f"/qq-webhook/{SECRET}", json=payload()).status_code == 200
    assert inbox.process_pending_once(now=2000) == 0

    with db_cursor() as cur:
        row = cur.execute(
            "SELECT status, attempts, last_error FROM qui_quo_events"
        ).fetchone()
    assert row[0] == "retry"
    assert row[1] == 1
    assert row[2] == "OperationalError"

    with db_cursor(commit=True) as cur:
        cur.execute(
            """
            CREATE TABLE crm_activities (
                activity_id TEXT PRIMARY KEY,
                request_id TEXT NOT NULL,
                activity_type TEXT NOT NULL,
                summary TEXT,
                created_at INTEGER NOT NULL
            )
            """
        )
        cur.execute("UPDATE qui_quo_events SET next_retry_at=0")

    assert inbox.process_pending_once(now=2003) == 1
    with db_cursor() as cur:
        assert cur.execute(
            "SELECT status FROM qui_quo_events"
        ).fetchone()[0] == "processed"


def test_cleanup_removes_old_event_and_quote_link(qq):
    client, db_cursor, _ = qq
    assert client.post(
        f"/qq-webhook/{SECRET}",
        json=payload("quote_open"),
    ).status_code == 200
    with db_cursor(commit=True) as cur:
        link_quote(
            cur.connection,
            "QQ-1234",
            "request-3",
            "main",
            now=100,
        )
        cur.execute("UPDATE qui_quo_events SET received_at=100")
        cleanup_before(cur.connection, 200)

    with db_cursor() as cur:
        assert cur.execute(
            "SELECT COUNT(*) FROM qui_quo_events"
        ).fetchone()[0] == 0
        assert cur.execute(
            "SELECT COUNT(*) FROM qui_quo_quote_links"
        ).fetchone()[0] == 0
