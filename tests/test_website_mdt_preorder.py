"""Regression tests for checkpointed Website -> MDT preorder delivery."""

import os

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
from shared import website_mdt_preorder as structured


@pytest.fixture(autouse=True)
def clean_state(monkeypatch):
    structured._migrate_schema(website_app)
    website_app._rate_hits.clear()
    monkeypatch.setattr(bot, "DEMO_MODE", False)
    monkeypatch.setattr(bot, "MDT_ENABLED", True)
    monkeypatch.setattr(bot, "MDT_MANAGER_IDS", [])
    monkeypatch.setattr(bot, "_mdt_country_cache", {"турция": 205})
    with bot._db_cursor(commit=True) as cur:
        cur.execute("DELETE FROM website_leads")


def _store(request_id: str):
    raw = {
        "requestId": request_id,
        "name": "TEST Structured",
        "phone": "+79990000004",
        "destination": "Турция",
        "origin": "Москва",
        "dates": "15–22 октября 2026",
        "people": "2",
        "budget": "150000",
        "consent": True,
        "company": "",
    }
    payload, error = website_app._validate_payload(raw)
    assert error is None
    lead_id, duplicate = website_app._store_lead(payload)
    assert duplicate is False
    return lead_id


def test_structured_delivery_maps_country_dates_and_sole_manager(monkeypatch):
    lead_id = _store("website-structured-0001")
    calls = []

    def request(method, params):
        calls.append((method, dict(params)))
        if method == "get-manager-list":
            return [{"id": 77, "dismissed": 0}]
        if method == "get-tourist-temp-list":
            return []
        if method == "add-tourist-temp":
            return {"id": 501}
        if method == "get-preorder-list":
            return []
        if method == "create-preorder":
            return {"id": 601}
        raise AssertionError(method)

    monkeypatch.setattr(bot, "_mdt_request", request)

    assert structured._deliver_structured_preorder(website_app, lead_id) is True

    create = next(params for method, params in calls if method == "create-preorder")
    assert create["country_id1"] == 205
    assert create["flightdate_from"] == "2026-10-15"
    assert create["flightdate_to"] == "2026-10-22"
    assert create["persons"] == 2
    assert create["price_to"] == 150000
    assert create["preorder_manager_id"] == 77
    assert f"web-lead-{lead_id}" in create["comment"]

    tourist = next(params for method, params in calls if method == "add-tourist-temp")
    assert tourist["manager_id"] == 77
    assert f"web-lead-{lead_id}" in tourist["tags"]

    with bot._db_cursor() as cur:
        cur.execute(
            """
            SELECT mdt_status, mdt_attempts, mdt_tourist_id,
                   mdt_preorder_id, mdt_manager_id, mdt_next_retry_at
            FROM website_leads WHERE id=?
            """,
            (lead_id,),
        )
        row = cur.fetchone()
    assert tuple(row) == ("synced", 1, 501, 601, 77, None)


def test_retry_reuses_tourist_and_recovers_ambiguous_preorder(monkeypatch):
    lead_id = _store("website-structured-0002")
    calls = []
    preorder_checks = 0

    def request(method, params):
        nonlocal preorder_checks
        calls.append((method, dict(params)))
        if method == "get-manager-list":
            # More than one active manager: Website must remain unassigned.
            return [{"id": 77, "dismissed": 0}, {"id": 88, "dismissed": 0}]
        if method == "get-tourist-temp-list":
            return []
        if method == "add-tourist-temp":
            return {"id": 502}
        if method == "get-preorder-list":
            preorder_checks += 1
            if preorder_checks == 1:
                return []
            return [
                {
                    "id": 602,
                    "tourist_id": 502,
                    "comment": f"ID заявки бота: web-lead-{lead_id}",
                }
            ]
        if method == "create-preorder":
            # Simulate a lost/ambiguous response after MDT may have written it.
            return None
        raise AssertionError(method)

    monkeypatch.setattr(bot, "_mdt_request", request)

    assert structured._deliver_structured_preorder(website_app, lead_id) is False
    assert structured._deliver_structured_preorder(website_app, lead_id) is True

    methods = [method for method, _ in calls]
    assert methods.count("add-tourist-temp") == 1
    assert methods.count("create-preorder") == 1
    assert methods.count("get-manager-list") == 1

    with bot._db_cursor() as cur:
        cur.execute(
            """
            SELECT mdt_status, mdt_attempts, mdt_tourist_id,
                   mdt_preorder_id, mdt_manager_id
            FROM website_leads WHERE id=?
            """,
            (lead_id,),
        )
        row = cur.fetchone()
    assert tuple(row) == ("synced", 2, 502, 602, 0)


def test_result_only_preorder_success_does_not_retry(monkeypatch):
    lead_id = _store("website-structured-0003")
    calls = []

    def request(method, params):
        calls.append((method, dict(params)))
        if method == "get-manager-list":
            return []
        if method == "get-tourist-temp-list":
            return []
        if method == "add-tourist-temp":
            return {"id": 503}
        if method == "get-preorder-list":
            return []
        if method == "create-preorder":
            return {"result": "ok"}
        raise AssertionError(method)

    monkeypatch.setattr(bot, "_mdt_request", request)

    assert structured._deliver_structured_preorder(website_app, lead_id) is True
    assert [method for method, _ in calls].count("create-preorder") == 1

    with bot._db_cursor() as cur:
        cur.execute(
            "SELECT mdt_status, mdt_attempts, mdt_next_retry_at FROM website_leads WHERE id=?",
            (lead_id,),
        )
        row = cur.fetchone()
    assert tuple(row) == ("synced", 1, None)
