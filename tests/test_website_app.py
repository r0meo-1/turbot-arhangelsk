"""Tests for the public Aprel Tour website lead endpoint."""

import os

# Keep imports deterministic and keep the website retry thread out of pytest.
os.environ.setdefault("BOT_TOKEN", "dummy-token")
os.environ.setdefault("ADMIN_ID", "999")
os.environ.setdefault("TELEGRAM_SECRET_TOKEN", "secret123")
os.environ.setdefault("DIALOG_TIMEOUT_HOURS", "0")
os.environ.setdefault("TUTU_ENABLED", "false")
os.environ.setdefault("DEMO_MODE", "false")
os.environ.setdefault("WEBSITE_LEAD_WORKER_ENABLED", "false")

import pytest

import bot
import website_app


ORIGIN = "https://r0meo1.ru"


def _payload(request_id="website-test-0001"):
    return {
        "requestId": request_id,
        "name": "Роман",
        "phone": "+79161234567",
        "destination": "Турция",
        "origin": "Москва",
        "dates": "15–22 октября 2026",
        "people": "2",
        "budget": "150000",
        "consent": True,
        "company": "",
        "utm_source": "vk",
        "utm_medium": "social",
        "utm_campaign": "autumn",
    }


@pytest.fixture(autouse=True)
def clean_website_state(monkeypatch):
    website_app._rate_hits.clear()
    monkeypatch.setattr(bot, "DEMO_MODE", False)
    with bot._db_cursor(commit=True) as cur:
        cur.execute("DELETE FROM website_leads")


@pytest.fixture
def client():
    return website_app.app.test_client()


def test_website_lead_cors_preflight(client):
    response = client.options("/website/lead", headers={"Origin": ORIGIN})
    assert response.status_code == 204
    assert response.headers["Access-Control-Allow-Origin"] == ORIGIN
    assert "POST" in response.headers["Access-Control-Allow-Methods"]


def test_website_lead_rejects_unknown_origin(client):
    response = client.post(
        "/website/lead",
        headers={"Origin": "https://evil.example"},
        json=_payload(),
    )
    assert response.status_code == 403
    assert response.get_json()["error"] == "origin_not_allowed"


def test_website_lead_requires_consent(client):
    payload = _payload()
    payload["consent"] = False
    response = client.post("/website/lead", headers={"Origin": ORIGIN}, json=payload)
    assert response.status_code == 400
    assert response.get_json()["error"] == "consent_required"


def test_website_honeypot_accepts_without_storing(client):
    payload = _payload()
    payload["company"] = "spam-bot-inc"
    response = client.post("/website/lead", headers={"Origin": ORIGIN}, json=payload)
    assert response.status_code == 202
    assert response.get_json()["ok"] is True
    with bot._db_cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM website_leads")
        assert cur.fetchone()[0] == 0


def test_website_lead_is_stored_before_async_delivery(client, monkeypatch):
    kicked = []
    monkeypatch.setattr(
        website_app,
        "_kick_delivery",
        lambda lead_id, payload: kicked.append((lead_id, dict(payload))),
    )

    response = client.post(
        "/website/lead",
        headers={"Origin": ORIGIN, "X-Forwarded-For": "203.0.113.10"},
        json=_payload(),
    )
    assert response.status_code == 202
    body = response.get_json()
    assert body["ok"] is True
    assert body["duplicate"] is False
    assert kicked and kicked[0][0] == body["leadId"]

    with bot._db_cursor() as cur:
        cur.execute(
            """
            SELECT name, phone, destination, origin, people, budget, consent_at,
                   utm_source, utm_medium, utm_campaign, mdt_status
            FROM website_leads WHERE id=?
            """,
            (body["leadId"],),
        )
        row = cur.fetchone()
    assert row[0] == "Роман"
    assert row[1] == "+79161234567"
    assert row[2] == "Турция"
    assert row[3] == "Москва"
    assert row[4] == "2"
    assert row[5] == 150000
    assert row[6] > 0
    assert row[7:10] == ("vk", "social", "autumn")
    assert row[10] == "pending"


def test_website_request_id_is_idempotent(client, monkeypatch):
    monkeypatch.setattr(website_app, "_kick_delivery", lambda *args, **kwargs: None)
    payload = _payload("website-idempotent-0001")

    first = client.post("/website/lead", headers={"Origin": ORIGIN}, json=payload)
    second = client.post("/website/lead", headers={"Origin": ORIGIN}, json=payload)

    assert first.status_code == 202
    assert second.status_code == 200
    assert second.get_json()["duplicate"] is True
    assert first.get_json()["leadId"] == second.get_json()["leadId"]
    with bot._db_cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM website_leads")
        assert cur.fetchone()[0] == 1


def test_website_delivery_uses_website_source_and_marks_synced(monkeypatch):
    payload, error = website_app._validate_payload(_payload("website-mdt-0001"))
    assert error is None
    assert payload is not None
    lead_id, duplicate = website_app._store_lead(payload)
    assert duplicate is False

    calls = []
    monkeypatch.setattr(bot, "MDT_ENABLED", True)
    monkeypatch.setattr(bot, "DEMO_MODE", False)
    monkeypatch.setattr(
        bot,
        "_mdt_request",
        lambda method, params: calls.append((method, params)) or {"id": 1823},
    )

    assert website_app._deliver_lead(lead_id) is True
    assert calls[0][0] == "add-lead"
    params = calls[0][1]
    assert params["source"] == "Website Aprel Tour"
    assert params["external_lead_id"] == f"web-lead-{lead_id}"
    fields = {field["name"]: field["values"][0] for field in params["fields"]}
    assert fields["Направление"] == "Турция"
    assert fields["Вылет"] == "Москва"
    assert fields["Бюджет"] == "150000 ₽ на всю поездку"

    with bot._db_cursor() as cur:
        cur.execute(
            "SELECT mdt_status, mdt_attempts, mdt_synced_at FROM website_leads WHERE id=?",
            (lead_id,),
        )
        status, attempts, synced_at = cur.fetchone()
    assert status == "synced"
    assert attempts == 1
    assert synced_at > 0


def test_website_owner_notification_retries_until_synced(monkeypatch):
    payload, error = website_app._validate_payload(_payload("website-owner-0001"))
    assert error is None
    assert payload is not None
    lead_id, duplicate = website_app._store_lead(payload)
    assert duplicate is False

    outcomes = iter([False, True])
    sent = []
    monkeypatch.setattr(
        bot,
        "send_lead_owner_vk",
        lambda text: sent.append(text) or next(outcomes),
    )

    assert website_app._deliver_owner_notification(lead_id) is False
    with bot._db_cursor() as cur:
        cur.execute(
            """
            SELECT owner_status, owner_attempts, owner_next_retry_at, owner_notified_at
            FROM website_leads WHERE id=?
            """,
            (lead_id,),
        )
        status, attempts, next_retry_at, notified_at = cur.fetchone()
    assert status == "pending"
    assert attempts == 1
    assert next_retry_at is not None
    assert notified_at is None
    assert sent and "Новая заявка с сайта" in sent[0]
    assert "Tourvisor PRO:" in sent[0]
    assert "Sletat PRO:" in sent[0]
    assert "Qui-Quo:" in sent[0]

    assert website_app._deliver_owner_notification(lead_id) is True
    with bot._db_cursor() as cur:
        cur.execute(
            """
            SELECT owner_status, owner_attempts, owner_next_retry_at, owner_notified_at
            FROM website_leads WHERE id=?
            """,
            (lead_id,),
        )
        status, attempts, next_retry_at, notified_at = cur.fetchone()
    assert status == "synced"
    assert attempts == 2
    assert next_retry_at is None
    assert notified_at is not None
