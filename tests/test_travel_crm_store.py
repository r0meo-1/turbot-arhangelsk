import sqlite3
from contextlib import closing
from datetime import datetime, timedelta

import pytest

from shared.travel_crm import (
    Activity,
    ActivityType,
    Attribution,
    BookingOutcome,
    BudgetType,
    Child,
    ManagerTask,
    OutcomeStatus,
    Quote,
    QuoteReaction,
    QuoteReactionEvent,
    TaskStatus,
    TaskType,
    TripRequest,
)
from shared.travel_crm_store import (
    append_activity,
    append_quote,
    append_quote_reaction,
    delete_for_lead_ids,
    due_tasks,
    ensure_initial_task,
    init_schema,
    load_timeline,
    set_outcome,
    set_task_status,
    upsert_request,
    upsert_task,
)


@pytest.fixture
def raw_conn():
    with closing(sqlite3.connect(":memory:")) as connection:
        yield connection


@pytest.fixture
def conn(raw_conn):
    init_schema(raw_conn.cursor())
    return raw_conn


def _request():
    return TripRequest(
        request_id="synthetic-lead-1",
        departure_city="Москва",
        departure_airport="SVO",
        adults=2,
        children=(Child(4), Child(9)),
        flexible_dates=True,
        nights_min=9,
        nights_max=12,
        budget_amount=250000,
        budget_type=BudgetType.MAX,
        meal_plans=("AI", "HB"),
        primary_destination="Вьетнам",
        alternative_destinations=("Таиланд",),
        attribution=Attribution(
            source_tag="video_pain",
            channel="telegram",
            campaign="winter_test",
        ),
    )


def test_schema_is_idempotent_and_preserves_attribution(conn):
    init_schema(conn.cursor())
    request = _request()

    upsert_request(conn, request, lead_id=123, now=datetime(2026, 9, 21, 10, 0))

    row = conn.execute(
        "SELECT lead_id, source_tag, channel, campaign FROM crm_trip_requests"
    ).fetchone()
    assert row == (123, "video_pain", "telegram", "winter_test")


def test_schema_adds_manager_assignment_columns_to_existing_request_table(raw_conn):
    conn = raw_conn
    conn.execute(
        """
        CREATE TABLE crm_trip_requests (
            request_id TEXT PRIMARY KEY,
            lead_id INTEGER,
            payload_json TEXT NOT NULL,
            source_tag TEXT,
            channel TEXT,
            campaign TEXT,
            created_at INTEGER NOT NULL,
            updated_at INTEGER NOT NULL
        )
        """
    )

    init_schema(conn.cursor())

    columns = {
        row[1] for row in conn.execute("PRAGMA table_info(crm_trip_requests)")
    }
    assert {"assigned_manager_id", "assigned_manager_name", "assigned_at"} <= columns


def test_timeline_round_trip_keeps_append_only_history(conn):
    request = _request()
    upsert_request(conn, request)

    q1 = Quote(
        quote_id="q1",
        request_id=request.request_id,
        hotel="Synthetic Resort A",
        operator="Demo Operator",
        price_amount=210000,
        calculated_at=datetime(2026, 9, 21, 10, 0),
        reaction=QuoteReaction.TOO_EXPENSIVE,
    )
    q2 = Quote(
        quote_id="q2",
        request_id=request.request_id,
        hotel="Synthetic Resort B",
        operator="Demo Operator",
        price_amount=195000,
        calculated_at=datetime(2026, 9, 21, 11, 0),
        reaction=QuoteReaction.THINKING,
    )
    append_quote(conn, q1)
    append_quote(conn, q2)
    append_activity(
        conn,
        Activity(
            activity_id="a1",
            request_id=request.request_id,
            type=ActivityType.NOTE,
            summary="Клиент просит вариант дешевле",
            created_at=datetime(2026, 9, 21, 11, 5),
        ),
    )
    set_outcome(
        conn,
        request.request_id,
        BookingOutcome(
            status=OutcomeStatus.PAUSED,
            reason="думает",
            decided_at=datetime(2026, 9, 21, 11, 10),
        ),
    )

    timeline = load_timeline(conn, request.request_id)

    assert timeline is not None
    assert [item.quote_id for item in timeline.quotes] == ["q1", "q2"]
    assert timeline.quotes[0].reaction is QuoteReaction.TOO_EXPENSIVE
    assert timeline.activities[0].summary == "Клиент просит вариант дешевле"
    assert timeline.outcome is not None
    assert timeline.outcome.reason == "думает"
    assert [child.age for child in timeline.request.children] == [4, 9]


def test_duplicate_quote_id_is_rejected_instead_of_overwriting_history(conn):
    request = _request()
    upsert_request(conn, request)
    quote = Quote(
        quote_id="q1",
        request_id=request.request_id,
        hotel="Synthetic Resort",
        price_amount=100000,
    )
    append_quote(conn, quote)

    with pytest.raises(sqlite3.IntegrityError):
        append_quote(conn, quote)


def test_due_tasks_returns_today_queue_by_priority(conn):
    request = _request()
    upsert_request(conn, request)
    now = datetime(2026, 9, 21, 12, 0)
    upsert_task(
        conn,
        ManagerTask(
            task_id="callback",
            request_id=request.request_id,
            type=TaskType.CALL_BACK,
            due_at=now - timedelta(minutes=10),
            created_at=now - timedelta(hours=2),
            priority=2,
        ),
    )
    upsert_task(
        conn,
        ManagerTask(
            task_id="send",
            request_id=request.request_id,
            type=TaskType.SEND_OPTIONS,
            due_at=now - timedelta(minutes=5),
            created_at=now - timedelta(hours=1),
            priority=1,
        ),
    )
    upsert_task(
        conn,
        ManagerTask(
            task_id="future",
            request_id=request.request_id,
            type=TaskType.DOCUMENTS,
            due_at=now + timedelta(hours=1),
            created_at=now,
            priority=1,
        ),
    )

    assert [task.task_id for task in due_tasks(conn, now)] == ["send", "callback"]


def test_delete_for_lead_ids_erases_whole_crm_timeline(conn):
    request = _request()
    upsert_request(conn, request, lead_id=77)
    append_quote(
        conn,
        Quote(
            quote_id="delete-q",
            request_id=request.request_id,
            hotel="Synthetic Resort",
            price_amount=123000,
        ),
    )
    append_activity(
        conn,
        Activity(
            activity_id="delete-a",
            request_id=request.request_id,
            type=ActivityType.NOTE,
            summary="synthetic note",
            created_at=datetime(2026, 9, 21, 10, 0),
        ),
    )

    assert delete_for_lead_ids(conn, [77]) == 1
    assert load_timeline(conn, request.request_id) is None
    assert conn.execute("SELECT COUNT(*) FROM crm_quotes").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM crm_activities").fetchone()[0] == 0



def test_quote_reaction_history_is_append_only_and_round_trips(conn):
    request = _request()
    upsert_request(conn, request)
    quote = Quote(
        quote_id="reaction-q",
        request_id=request.request_id,
        hotel="Synthetic Resort",
        price_amount=190000,
        reaction=QuoteReaction.SENT,
    )
    append_quote(conn, quote)
    append_quote_reaction(
        conn,
        QuoteReactionEvent(
            event_id="reaction-1",
            quote_id=quote.quote_id,
            request_id=request.request_id,
            reaction=QuoteReaction.TOO_EXPENSIVE,
            note="Просит дешевле",
            created_at=datetime(2026, 9, 21, 12, 0),
        ),
    )
    append_quote_reaction(
        conn,
        QuoteReactionEvent(
            event_id="reaction-2",
            quote_id=quote.quote_id,
            request_id=request.request_id,
            reaction=QuoteReaction.WANTS_ALTERNATIVE,
            note="Нужен другой отель",
            created_at=datetime(2026, 9, 21, 12, 5),
        ),
    )

    timeline = load_timeline(conn, request.request_id)

    assert timeline is not None
    assert [event.event_id for event in timeline.quote_reactions] == [
        "reaction-1",
        "reaction-2",
    ]
    assert timeline.quotes[0].reaction is QuoteReaction.SENT
    assert timeline.quote_reactions[-1].reaction is QuoteReaction.WANTS_ALTERNATIVE


def test_initial_task_is_idempotent_and_can_be_completed(conn):
    request = _request()
    upsert_request(conn, request)
    now = datetime(2026, 9, 21, 9, 0)

    first = ensure_initial_task(conn, request.request_id, now)
    second = ensure_initial_task(conn, request.request_id, now + timedelta(minutes=1))

    assert first == second
    rows = conn.execute(
        "SELECT task_id, status FROM crm_tasks WHERE request_id=?",
        (request.request_id,),
    ).fetchall()
    assert rows == [(first, "todo")]
    assert set_task_status(conn, first, TaskStatus.DONE) is True
    assert due_tasks(conn, now + timedelta(hours=1)) == []


def test_channel_scoped_delete_does_not_erase_same_numeric_id_from_another_channel(conn):
    telegram = _request()
    website = TripRequest(
        request_id="web-lead-77",
        departure_city="Москва",
        adults=2,
        primary_destination="Таиланд",
        attribution=Attribution(channel="website", source_tag="web_test"),
    )
    upsert_request(conn, telegram, lead_id=77)
    upsert_request(conn, website, lead_id=77)

    assert delete_for_lead_ids(conn, [77], channel="telegram") == 1
    assert load_timeline(conn, telegram.request_id) is None
    assert load_timeline(conn, website.request_id) is not None
