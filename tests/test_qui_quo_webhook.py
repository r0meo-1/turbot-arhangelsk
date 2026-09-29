import json
import sqlite3
from contextlib import contextmanager

import pytest
from flask import Flask

from shared.qui_quo_webhook import QuiQuoInbox, create_blueprint


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
            CREATE TABLE crm_quotes (
                quote_id TEXT PRIMARY KEY,
                request_id TEXT NOT NULL
            )
            """
        )
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


def test_event_projects_once_to_existing_crm_quote(qq):
    client, db_cursor, inbox = qq
    with db_cursor(commit=True) as cur:
        cur.execute(
            "INSERT INTO crm_quotes(quote_id, request_id) VALUES (?, ?)",
            ("QQ-1234", "request-1"),
        )

    assert client.post(f"/qq-webhook/{SECRET}", json=payload("item_order")).status_code == 200
    assert inbox.process_pending_once(now=1000) == 1

    # Same semantic user action is acknowledged but cannot duplicate activity.
    assert client.post(f"/qq-webhook/{SECRET}", json=payload("item_order")).status_code == 200
    assert inbox.process_pending_once(now=1001) == 0

    with db_cursor() as cur:
        activities = cur.execute(
            "SELECT request_id, activity_type, summary FROM crm_activities"
        ).fetchall()
        event = cur.execute(
            "SELECT status, request_id FROM qui_quo_events"
        ).fetchone()

    assert len(activities) == 1
    assert activities[0][0] == "request-1"
    assert activities[0][1] == "note"
    assert "item_order" in activities[0][2]
    assert tuple(event) == ("processed", "request-1")


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
        cur.execute(
            "INSERT INTO crm_quotes(quote_id, request_id) VALUES (?, ?)",
            ("QQ-1234", "request-2"),
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
