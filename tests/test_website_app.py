"""Tests for the public Aprel Tour website lead endpoint."""

import os
from datetime import datetime, timedelta

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
from shared.travel_crm import (
    Activity,
    ActivityType,
    Attribution,
    ManagerTask,
    Quote,
    QuoteReaction,
    TaskType,
    TripRequest,
)
from shared import travel_crm_store as crm_store


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
        cur.execute("DELETE FROM acquisition_funnel_events")
        for table in ("crm_quotes", "crm_activities", "crm_tasks", "crm_outcomes", "crm_trip_requests"):
            cur.execute(f"DELETE FROM {table}")


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
    funnel = bot._funnel_health()
    website_source = funnel["channels"]["website"]["vk:autumn"]
    assert website_source["lead"]["accepted"] == 1


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

    funnel = bot._funnel_health()
    manager = funnel["channels"]["website"]["vk:autumn"]["manager"]
    assert manager["failed"] == 1
    assert manager["delivered"] == 1



def test_agent_extension_requires_pairing_token(client, monkeypatch):
    monkeypatch.setattr(website_app, "_AGENT_EXTENSION_TOKEN", "agent-secret")
    response = client.post(
        "/agent-extension/lead",
        headers={"Origin": "chrome-extension://abcdefghijklmnop"},
        json=_payload("agent-test-0001"),
    )
    assert response.status_code == 401
    assert response.get_json()["error"] == "unauthorized"


def test_agent_extension_stores_candidates_and_delivers(client, monkeypatch):
    monkeypatch.setattr(website_app, "_AGENT_EXTENSION_TOKEN", "agent-secret")
    kicked = []
    monkeypatch.setattr(
        website_app,
        "_kick_delivery",
        lambda lead_id, payload: kicked.append((lead_id, dict(payload))),
    )
    payload = _payload("agent-test-0002")
    payload.update({
        "active_service": "pro.tourvisor.ru",
        "candidates": [
            {
                "title": "Hotel One",
                "url": "https://operator.example/tour/1",
                "selection": "10 ночей · AI · 219 000 ₽",
            },
            {
                "title": "Hotel Two",
                "url": "https://operator.example/tour/2",
                "selection": "11 ночей · BB · 205 000 ₽",
            },
        ],
    })

    response = client.post(
        "/agent-extension/lead",
        headers={
            "Origin": "chrome-extension://abcdefghijklmnop",
            "Authorization": "Bearer agent-secret",
        },
        json=payload,
    )

    assert response.status_code == 202
    body = response.get_json()
    assert body["ok"] is True
    assert kicked and kicked[0][0] == body["leadId"]
    stored_payload = kicked[0][1]
    assert stored_payload["utm_source"] == "agent_extension"
    assert stored_payload["utm_medium"] == "browser_sidepanel"
    assert stored_payload["utm_content"] == "pro.tourvisor.ru"
    assert len(stored_payload["agent_candidates"]) == 2
    assert stored_payload["agent_candidates"][0]["title"] == "Hotel One"

    text = website_app._owner_notification_text(body["leadId"], stored_payload)
    assert "Кандидаты из Agent Desk" in text
    assert "Hotel One" in text
    assert "219 000 ₽" in text



def test_agent_extension_lists_and_updates_crm_status(client, monkeypatch):
    monkeypatch.setattr(website_app, "_AGENT_EXTENSION_TOKEN", "agent-secret")
    monkeypatch.setattr(website_app, "_kick_delivery", lambda *args, **kwargs: None)

    create = client.post(
        "/agent-extension/lead",
        headers={
            "Origin": "chrome-extension://abcdefghijklmnop",
            "Authorization": "Bearer agent-secret",
        },
        json={
            **_payload("agent-crm-0001"),
            "candidates": [{
                "title": "Hotel One",
                "url": "https://operator.example/tour/1",
                "selection": "10 ночей · AI",
            }],
        },
    )
    assert create.status_code == 202
    lead_id = create.get_json()["leadId"]

    listing = client.get(
        "/agent-extension/leads?limit=10",
        headers={
            "Origin": "chrome-extension://abcdefghijklmnop",
            "Authorization": "Bearer agent-secret",
        },
    )
    assert listing.status_code == 200
    leads = listing.get_json()["leads"]
    assert leads[0]["id"] == lead_id
    assert leads[0]["status"] == "new"
    assert leads[0]["candidateCount"] == 1

    update = client.post(
        "/agent-extension/status",
        headers={
            "Origin": "chrome-extension://abcdefghijklmnop",
            "Authorization": "Bearer agent-secret",
        },
        json={
            "leadId": lead_id,
            "status": "working",
            "note": "Ждём ответ клиента",
            "followUpOn": "2026-09-21",
        },
    )
    assert update.status_code == 200
    assert update.get_json()["status"] == "working"
    assert update.get_json()["followUpOn"] == "2026-09-21"

    with bot._db_cursor() as cur:
        cur.execute(
            """
            SELECT crm_status, crm_note, crm_followup_on, crm_updated_at
            FROM website_leads WHERE id=?
            """,
            (lead_id,),
        )
        status, note, follow_up_on, updated_at = cur.fetchone()
    assert status == "working"
    assert note == "Ждём ответ клиента"
    assert follow_up_on == "2026-09-21"
    assert updated_at > 0

    listing2 = client.get(
        "/agent-extension/leads?limit=10",
        headers={
            "Origin": "chrome-extension://abcdefghijklmnop",
            "Authorization": "Bearer agent-secret",
        },
    )
    assert listing2.status_code == 200
    assert listing2.get_json()["leads"][0]["followUpOn"] == "2026-09-21"



def test_agent_extension_rejects_invalid_followup_date(client, monkeypatch):
    monkeypatch.setattr(website_app, "_AGENT_EXTENSION_TOKEN", "agent-secret")
    response = client.post(
        "/agent-extension/status",
        headers={
            "Origin": "chrome-extension://abcdefghijklmnop",
            "Authorization": "Bearer agent-secret",
        },
        json={
            "leadId": 1,
            "status": "working",
            "note": "",
            "followUpOn": "2026-99-77",
        },
    )
    assert response.status_code == 400
    assert response.get_json()["error"] == "invalid_followup_date"


def test_agent_extension_csv_export_is_authorized_and_contains_lead(client, monkeypatch):
    monkeypatch.setattr(website_app, "_AGENT_EXTENSION_TOKEN", "agent-secret")
    monkeypatch.setattr(website_app, "_kick_delivery", lambda *args, **kwargs: None)

    create = client.post(
        "/agent-extension/lead",
        headers={
            "Origin": "chrome-extension://abcdefghijklmnop",
            "Authorization": "Bearer agent-secret",
        },
        json=_payload("agent-csv-0001"),
    )
    assert create.status_code == 202

    denied = client.get(
        "/agent-extension/export.csv",
        headers={"Origin": "chrome-extension://abcdefghijklmnop"},
    )
    assert denied.status_code == 401

    response = client.get(
        "/agent-extension/export.csv",
        headers={
            "Origin": "chrome-extension://abcdefghijklmnop",
            "Authorization": "Bearer agent-secret",
        },
    )
    assert response.status_code == 200
    assert response.headers["Content-Type"].startswith("text/csv")
    assert "attachment;" in response.headers["Content-Disposition"]
    text = response.get_data(as_text=True)
    assert "Роман" in text
    assert "+79161234567" in text
    assert "Турция" in text


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (
            "https://operator.example/tour/1?hotel=abc&access_token=SECRET&SESSION=s1#frag",
            "https://operator.example/tour/1?hotel=abc",
        ),
        (
            "https://operator.example/tour/1?AccessToken=x&apiKey=y&safe=1",
            "https://operator.example/tour/1?safe=1",
        ),
        ("javascript:alert(1)", ""),
        ("file:///tmp/tour.html", ""),
        ("chrome-extension://abcdefghijkl/page.html", ""),
        ("not a url", ""),
        ("https://user:pass@operator.example/tour", ""),
    ],
)
def test_agent_candidate_url_sanitizer(raw, expected):
    assert website_app._sanitize_candidate_url(raw) == expected


def test_agent_extension_sanitizes_candidate_before_delivery(client, monkeypatch):
    monkeypatch.setattr(website_app, "_AGENT_EXTENSION_TOKEN", "agent-secret")
    kicked = []
    monkeypatch.setattr(
        website_app,
        "_kick_delivery",
        lambda lead_id, payload: kicked.append((lead_id, dict(payload))),
    )
    payload = _payload("agent-url-safe-0001")
    payload["candidates"] = [{
        "title": "Hotel Secret",
        "url": (
            "https://operator.example/tour/1?hotel=abc&"
            "AccessToken=secret&SESSION=session&apiKey=key#details"
        ),
        "selection": "10 ночей · 219 000 ₽",
    }]

    response = client.post(
        "/agent-extension/lead",
        headers={
            "Origin": "chrome-extension://abcdefghijklmnop",
            "Authorization": "Bearer agent-secret",
        },
        json=payload,
    )

    assert response.status_code == 202
    stored = kicked[0][1]["agent_candidates"][0]
    assert stored["title"] == "Hotel Secret"
    assert stored["selection"] == "10 ночей · 219 000 ₽"
    assert stored["url"] == "https://operator.example/tour/1?hotel=abc"

    message = website_app._owner_notification_text(response.get_json()["leadId"], kicked[0][1])
    assert "AccessToken" not in message
    assert "SESSION" not in message
    assert "apiKey" not in message
    assert "#details" not in message


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("=1+1", "'=1+1"),
        ("+cmd", "'+cmd"),
        ("-10+20", "'-10+20"),
        ("@SUM(A1:A2)", "'@SUM(A1:A2)"),
        ("   =1+1", "'   =1+1"),
        ("\t@SUM(A1:A2)", "'\t@SUM(A1:A2)"),
        ("\n+cmd", "'\n+cmd"),
        ("Обычная заметка", "Обычная заметка"),
        (" Турция", " Турция"),
    ],
)
def test_csv_safe_cell_blocks_formula_injection(raw, expected):
    assert website_app._csv_safe_cell(raw) == expected


def test_agent_extension_csv_export_escapes_formula_cells(client, monkeypatch):
    monkeypatch.setattr(website_app, "_AGENT_EXTENSION_TOKEN", "agent-secret")
    monkeypatch.setattr(website_app, "_kick_delivery", lambda *args, **kwargs: None)

    payload = _payload("agent-csv-formula-0001")
    payload["name"] = "=1+1"
    create = client.post(
        "/agent-extension/lead",
        headers={
            "Origin": "chrome-extension://abcdefghijklmnop",
            "Authorization": "Bearer agent-secret",
        },
        json=payload,
    )
    assert create.status_code == 202
    lead_id = create.get_json()["leadId"]

    update = client.post(
        "/agent-extension/status",
        headers={
            "Origin": "chrome-extension://abcdefghijklmnop",
            "Authorization": "Bearer agent-secret",
        },
        json={
            "leadId": lead_id,
            "status": "working",
            "note": "@SUM(A1:A2)",
            "followUpOn": "2026-09-21",
        },
    )
    assert update.status_code == 200

    response = client.get(
        "/agent-extension/export.csv",
        headers={
            "Origin": "chrome-extension://abcdefghijklmnop",
            "Authorization": "Bearer agent-secret",
        },
    )
    assert response.status_code == 200
    csv_text = response.get_data(as_text=True)
    assert "'=1+1" in csv_text
    assert "'@SUM(A1:A2)" in csv_text


def _agent_headers():
    return {
        "Origin": "chrome-extension://abcdefghijklmnop",
        "Authorization": "Bearer agent-secret",
    }


def _seed_crm_request():
    request = TripRequest(
        request_id="telegram-lead-7001",
        departure_city="Москва",
        adults=2,
        dates_text="10–20 января 2027",
        budget_amount=240000,
        primary_destination="Вьетнам",
        attribution=Attribution(
            source_tag="video_dream",
            channel="telegram",
            campaign="winter_2027",
        ),
    )
    with bot._db_cursor(commit=True) as cur:
        crm_store.upsert_request(cur.connection, request, lead_id=None)
    return request


def test_agent_crm_today_queue_and_timeline(client, monkeypatch):
    monkeypatch.setattr(website_app, "_AGENT_EXTENSION_TOKEN", "agent-secret")
    request = _seed_crm_request()
    now = datetime(2026, 9, 21, 12, 0, 0)

    with bot._db_cursor(commit=True) as cur:
        crm_store.upsert_task(
            cur.connection,
            ManagerTask(
                task_id="task-due",
                request_id=request.request_id,
                type=TaskType.CALL_BACK,
                due_at=now - timedelta(minutes=10),
                created_at=now - timedelta(hours=1),
                priority=1,
                note="Уточнить бюджет",
            ),
        )
        crm_store.upsert_task(
            cur.connection,
            ManagerTask(
                task_id="task-future",
                request_id=request.request_id,
                type=TaskType.DOCUMENTS,
                due_at=now + timedelta(hours=2),
                created_at=now,
                priority=2,
            ),
        )
        crm_store.append_quote(
            cur.connection,
            Quote(
                quote_id="quote-existing",
                request_id=request.request_id,
                hotel="Synthetic Beach Resort 5*",
                operator="Demo Operator",
                price_amount=215000,
                calculated_at=now - timedelta(hours=2),
                reaction=QuoteReaction.SENT,
            ),
        )
        crm_store.append_activity(
            cur.connection,
            Activity(
                activity_id="activity-existing",
                request_id=request.request_id,
                type=ActivityType.CALL,
                summary="Клиент попросил подумать",
                created_at=now - timedelta(hours=1),
            ),
        )

    today = client.get(
        "/agent-extension/crm/today?now=2026-09-21T12:00:00&limit=20",
        headers=_agent_headers(),
    )
    assert today.status_code == 200
    body = today.get_json()
    assert [item["taskId"] for item in body["tasks"]] == ["task-due"]
    assert body["tasks"][0]["request"]["destination"] == "Вьетнам"
    assert body["tasks"][0]["request"]["sourceTag"] == "video_dream"

    timeline = client.get(
        "/agent-extension/crm/timeline?requestId=telegram-lead-7001",
        headers=_agent_headers(),
    )
    assert timeline.status_code == 200
    data = timeline.get_json()["timeline"]
    assert data["request"]["primary_destination"] == "Вьетнам"
    assert data["quotes"][0]["quote_id"] == "quote-existing"
    assert data["lastContact"]["summary"] == "Клиент попросил подумать"


def test_agent_crm_manager_actions_are_persisted(client, monkeypatch):
    monkeypatch.setattr(website_app, "_AGENT_EXTENSION_TOKEN", "agent-secret")
    request = _seed_crm_request()
    headers = _agent_headers()

    quote = client.post(
        "/agent-extension/crm/quote",
        headers=headers,
        json={
            "requestId": request.request_id,
            "hotel": "Synthetic Family Resort 5*",
            "operator": "Demo Operator",
            "carrier": "Demo Air",
            "mealPlan": "AI",
            "priceAmount": 198000,
            "currency": "RUB",
            "reaction": "sent",
            "calculatedAt": "2026-09-21T10:00:00",
        },
    )
    assert quote.status_code == 200
    quote_id = quote.get_json()["quoteId"]

    reaction = client.post(
        "/agent-extension/crm/quote-reaction",
        headers=headers,
        json={"quoteId": quote_id, "reaction": "too_expensive"},
    )
    assert reaction.status_code == 200

    activity = client.post(
        "/agent-extension/crm/activity",
        headers=headers,
        json={
            "requestId": request.request_id,
            "type": "message_received",
            "summary": "Просит вариант дешевле",
            "createdAt": "2026-09-21T10:10:00",
        },
    )
    assert activity.status_code == 200

    task = client.post(
        "/agent-extension/crm/task",
        headers=headers,
        json={
            "requestId": request.request_id,
            "type": "build_selection",
            "dueAt": "2026-09-21T11:00:00",
            "priority": 1,
            "note": "Подобрать дешевле",
        },
    )
    assert task.status_code == 200
    task_id = task.get_json()["taskId"]

    done = client.post(
        "/agent-extension/crm/task-status",
        headers=headers,
        json={"taskId": task_id, "status": "done"},
    )
    assert done.status_code == 200

    outcome = client.post(
        "/agent-extension/crm/outcome",
        headers=headers,
        json={
            "requestId": request.request_id,
            "status": "paused",
            "reason": "думает",
            "decidedAt": "2026-09-21T10:15:00",
        },
    )
    assert outcome.status_code == 200

    timeline = client.get(
        "/agent-extension/crm/timeline?requestId=" + request.request_id,
        headers=headers,
    ).get_json()["timeline"]

    assert timeline["quotes"][0]["reaction"] == "too_expensive"
    assert any(item["summary"] == "Просит вариант дешевле" for item in timeline["activities"])
    assert any(item["summary"].startswith("quote:") for item in timeline["activities"])
    assert timeline["tasks"][0]["status"] == "done"
    assert timeline["outcome"]["status"] == "paused"
    assert timeline["outcome"]["reason"] == "думает"


def test_agent_crm_endpoints_require_pairing_token(client, monkeypatch):
    monkeypatch.setattr(website_app, "_AGENT_EXTENSION_TOKEN", "agent-secret")
    response = client.get(
        "/agent-extension/crm/today",
        headers={"Origin": "chrome-extension://abcdefghijklmnop"},
    )
    assert response.status_code == 401
    assert response.get_json()["error"] == "unauthorized"
