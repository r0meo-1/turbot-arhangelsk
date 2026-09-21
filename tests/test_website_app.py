"""Tests for the public Aprel Tour website lead endpoint."""

import os
import sqlite3
from datetime import datetime, timezone

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
from shared import travel_crm_store as crm_store
from shared.travel_crm import Attribution, TripRequest


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
def clean_website_state(monkeypatch, tmp_path):
    website_app._rate_hits.clear()
    monkeypatch.setattr(bot, "DEMO_MODE", False)
    monkeypatch.setattr(
        website_app,
        "_VK_DATABASE_PATH",
        str(tmp_path / "vk-agent-crm.sqlite"),
    )
    with bot._db_cursor(commit=True) as cur:
        cur.execute("DELETE FROM crm_quote_reactions")
        cur.execute("DELETE FROM crm_quotes")
        cur.execute("DELETE FROM crm_activities")
        cur.execute("DELETE FROM crm_tasks")
        cur.execute("DELETE FROM crm_outcomes")
        cur.execute("DELETE FROM crm_trip_requests")
        cur.execute("DELETE FROM website_leads")
        cur.execute("DELETE FROM acquisition_funnel_events")


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
    with bot._db_cursor() as cur:
        crm = cur.execute(
            """
            SELECT request_id, source_tag, channel, campaign
            FROM crm_trip_requests WHERE lead_id=? AND channel='website'
            """,
            (body["leadId"],),
        ).fetchone()
        task = cur.execute(
            """
            SELECT task_type, status FROM crm_tasks
            WHERE request_id=?
            """,
            (f"web-lead-{body['leadId']}",),
        ).fetchone()
    assert tuple(crm) == (
        f"web-lead-{body['leadId']}", "autumn", "website", "autumn"
    )
    assert tuple(task) == ("build_selection", "todo")
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

    with bot._db_cursor() as cur:
        timeline = website_app._travel_crm_store.load_timeline(
            cur.connection, f"web-lead-{lead_id}"
        )
    assert timeline is not None
    assert timeline.activities[-1].type.value == "status_change"
    assert "Ждём ответ клиента" in timeline.activities[-1].summary
    assert any(
        task.type.value == "next_contact"
        and task.due_at.date().isoformat() == "2026-09-21"
        for task in timeline.tasks
    )

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


def test_agent_extension_crm_today_quote_reaction_and_activity(client, monkeypatch):
    monkeypatch.setattr(website_app, "_AGENT_EXTENSION_TOKEN", "agent-secret")
    monkeypatch.setattr(website_app, "_kick_delivery", lambda *args, **kwargs: None)

    create = client.post(
        "/agent-extension/lead",
        headers=_agent_headers(),
        json=_payload("agent-crm-timeline-0001"),
    )
    assert create.status_code == 202
    lead_id = create.get_json()["leadId"]
    request_id = f"web-lead-{lead_id}"

    today = client.get(
        "/agent-extension/crm/today",
        headers=_agent_headers(),
    )
    assert today.status_code == 200
    tasks = today.get_json()["tasks"]
    initial = next(task for task in tasks if task["requestId"] == request_id)
    assert initial["type"] == "build_selection"
    assert initial["destination"] == "Турция"
    assert initial["sourceTag"] == "autumn"

    quote = client.post(
        "/agent-extension/crm/quote",
        headers=_agent_headers(),
        json={
            "requestId": request_id,
            "hotel": "Synthetic Family Resort 5*",
            "operator": "Demo Operator",
            "carrier": "Demo Air",
            "mealPlan": "AI",
            "priceAmount": 195000,
            "currency": "RUB",
        },
    )
    assert quote.status_code == 200
    quote_id = quote.get_json()["quoteId"]

    reaction = client.post(
        "/agent-extension/crm/reaction",
        headers=_agent_headers(),
        json={
            "quoteId": quote_id,
            "reaction": "too_expensive",
            "note": "Просит вариант дешевле",
        },
    )
    assert reaction.status_code == 200

    activity = client.post(
        "/agent-extension/crm/activity",
        headers=_agent_headers(),
        json={
            "requestId": request_id,
            "type": "call",
            "summary": "Созвонились, клиент думает до вечера",
        },
    )
    assert activity.status_code == 200

    follow = client.post(
        "/agent-extension/crm/task",
        headers=_agent_headers(),
        json={
            "requestId": request_id,
            "type": "next_contact",
            "dueAt": "2030-09-21T12:00:00",
            "priority": 2,
            "note": "Уточнить решение",
        },
    )
    assert follow.status_code == 200

    timeline = client.get(
        "/agent-extension/crm/timeline?requestId=" + request_id,
        headers=_agent_headers(),
    )
    assert timeline.status_code == 200
    payload = timeline.get_json()["timeline"]
    assert payload["request"]["attribution"]["channel"] == "website"
    assert payload["quotes"][0]["hotel"] == "Synthetic Family Resort 5*"
    assert payload["quote_reactions"][-1]["reaction"] == "too_expensive"
    assert payload["activities"][-1]["summary"] == "Созвонились, клиент думает до вечера"
    assert any(task["type"] == "next_contact" for task in payload["tasks"])

    done = client.post(
        "/agent-extension/crm/task",
        headers=_agent_headers(),
        json={"taskId": initial["taskId"], "status": "done"},
    )
    assert done.status_code == 200
    assert done.get_json()["status"] == "done"


def test_agent_extension_crm_today_uses_manager_local_day(client, monkeypatch):
    monkeypatch.setattr(website_app, "_AGENT_EXTENSION_TOKEN", "agent-secret")
    monkeypatch.setattr(website_app, "_kick_delivery", lambda *args, **kwargs: None)

    create = client.post(
        "/agent-extension/lead",
        headers=_agent_headers(),
        json=_payload("agent-crm-local-day-0001"),
    )
    assert create.status_code == 202
    request_id = f"web-lead-{create.get_json()['leadId']}"

    def epoch(value):
        return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp())

    with bot._db_cursor(commit=True) as cur:
        cur.execute(
            "UPDATE crm_tasks SET due_at=? WHERE request_id=? AND task_type='build_selection'",
            (epoch("2026-09-21T20:00:00Z"), request_id),
        )
        cur.execute(
            """
            INSERT INTO crm_tasks (
                task_id, request_id, task_type, due_at, created_at,
                priority, status, note
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "task-after-moscow-day",
                request_id,
                "next_contact",
                epoch("2026-09-21T21:30:00Z"),
                epoch("2026-09-20T21:30:00Z"),
                2,
                "todo",
                "Следующий локальный день",
            ),
        )

    response = client.get(
        "/agent-extension/crm/today"
        "?now=2026-09-20T21:30:00Z&tzOffsetMinutes=-180&limit=100",
        headers=_agent_headers(),
    )
    assert response.status_code == 200
    ids = {item["taskId"] for item in response.get_json()["tasks"]}
    assert f"{request_id}:build-selection" in ids
    assert "task-after-moscow-day" not in ids


def test_agent_extension_crm_unifies_main_and_vk_stores(client, monkeypatch):
    monkeypatch.setattr(website_app, "_AGENT_EXTENSION_TOKEN", "agent-secret")
    now = datetime(2026, 9, 21, 9, 0, 0)

    main_request = TripRequest(
        request_id="tg-lead-77",
        departure_city="Москва",
        adults=2,
        primary_destination="Таиланд",
        attribution=Attribution(
            source_tag="video_dream",
            channel="telegram",
        ),
    )
    with bot._db_cursor(commit=True) as cur:
        crm_store.upsert_request(
            cur.connection,
            main_request,
            lead_id=77,
            now=now,
        )
        crm_store.ensure_initial_task(
            cur.connection,
            main_request.request_id,
            now,
        )

    vk_path = website_app._agent_crm_db_path("vk")
    conn = sqlite3.connect(str(vk_path))
    try:
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        crm_store.init_schema(cur)
        cur.execute(
            """
            CREATE TABLE leads (
                id INTEGER PRIMARY KEY,
                first_name TEXT,
                phone TEXT,
                username TEXT
            )
            """
        )
        cur.execute(
            "INSERT INTO leads (id, first_name, phone, username) VALUES (?, ?, ?, ?)",
            (77, "VK Test", "", "vk_test"),
        )
        vk_request = TripRequest(
            request_id="vk-lead-77",
            departure_city="Архангельск",
            adults=2,
            primary_destination="Вьетнам",
            attribution=Attribution(
                source_tag="video_pain",
                channel="vk",
            ),
        )
        crm_store.upsert_request(
            conn,
            vk_request,
            lead_id=77,
            now=now,
        )
        crm_store.ensure_initial_task(
            conn,
            vk_request.request_id,
            now,
        )
        conn.commit()
    finally:
        conn.close()

    today = client.get(
        "/agent-extension/crm/today"
        "?now=2026-09-21T09:30:00Z&tzOffsetMinutes=0&limit=100",
        headers=_agent_headers(),
    )
    assert today.status_code == 200
    tasks = today.get_json()["tasks"]
    by_request = {item["requestId"]: item for item in tasks}
    assert by_request["tg-lead-77"]["sourceTag"] == "video_dream"
    assert by_request["vk-lead-77"]["sourceTag"] == "video_pain"
    assert by_request["vk-lead-77"]["client"]["name"] == "VK Test"

    timeline = client.get(
        "/agent-extension/crm/timeline?requestId=vk-lead-77",
        headers=_agent_headers(),
    )
    assert timeline.status_code == 200
    assert timeline.get_json()["timeline"]["request"]["primary_destination"] == "Вьетнам"

    quote = client.post(
        "/agent-extension/crm/quote",
        headers=_agent_headers(),
        json={
            "requestId": "vk-lead-77",
            "hotel": "Synthetic VK Resort 5*",
            "priceAmount": 210000,
            "currency": "RUB",
        },
    )
    assert quote.status_code == 200
    quote_id = quote.get_json()["quoteId"]

    reaction = client.post(
        "/agent-extension/crm/reaction",
        headers=_agent_headers(),
        json={
            "requestId": "vk-lead-77",
            "quoteId": quote_id,
            "reaction": "thinking",
            "note": "synthetic",
        },
    )
    assert reaction.status_code == 200

    initial_task = next(
        item for item in tasks if item["requestId"] == "vk-lead-77"
    )
    done = client.post(
        "/agent-extension/crm/task",
        headers=_agent_headers(),
        json={
            "requestId": "vk-lead-77",
            "taskId": initial_task["taskId"],
            "status": "done",
        },
    )
    assert done.status_code == 200

    conn = sqlite3.connect(str(vk_path))
    try:
        assert conn.execute(
            "SELECT COUNT(*) FROM crm_quotes WHERE request_id='vk-lead-77'"
        ).fetchone()[0] == 1
        assert conn.execute(
            "SELECT COUNT(*) FROM crm_quote_reactions WHERE request_id='vk-lead-77'"
        ).fetchone()[0] == 1
        assert conn.execute(
            "SELECT status FROM crm_tasks WHERE task_id=?",
            (initial_task["taskId"],),
        ).fetchone()[0] == "done"
    finally:
        conn.close()

    with bot._db_cursor() as cur:
        assert cur.execute(
            "SELECT COUNT(*) FROM crm_quotes WHERE request_id='vk-lead-77'"
        ).fetchone()[0] == 0


def test_agent_extension_crm_requires_pairing_token(client, monkeypatch):
    monkeypatch.setattr(website_app, "_AGENT_EXTENSION_TOKEN", "agent-secret")

    denied = client.get("/agent-extension/crm/today")
    assert denied.status_code == 401

    denied_timeline = client.get(
        "/agent-extension/crm/timeline?requestId=web-lead-1"
    )
    assert denied_timeline.status_code == 401


def test_agent_extension_won_status_closes_tasks_and_sets_outcome(client, monkeypatch):
    monkeypatch.setattr(website_app, "_AGENT_EXTENSION_TOKEN", "agent-secret")
    monkeypatch.setattr(website_app, "_kick_delivery", lambda *args, **kwargs: None)

    create = client.post(
        "/agent-extension/lead",
        headers=_agent_headers(),
        json=_payload("agent-crm-won-0001"),
    )
    lead_id = create.get_json()["leadId"]
    request_id = f"web-lead-{lead_id}"

    update = client.post(
        "/agent-extension/status",
        headers=_agent_headers(),
        json={
            "leadId": lead_id,
            "status": "won",
            "note": "Забронировано",
            "followUpOn": "",
        },
    )
    assert update.status_code == 200

    with bot._db_cursor() as cur:
        timeline = website_app._travel_crm_store.load_timeline(
            cur.connection, request_id
        )
    assert timeline is not None
    assert timeline.outcome is not None
    assert timeline.outcome.status.value == "won"
    assert timeline.outcome.reason == "Забронировано"
    assert all(task.status.value == "done" for task in timeline.tasks)
