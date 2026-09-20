"""SQLite persistence for TurBot's travel CRM domain.

The existing Telegram/VK lead tables stay intact. These tables are additive and
support the richer request/quote/task/outcome history without rewriting the
production funnel in one heroic migration. Humanity has tried those before.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any

from shared.travel_crm import (
    Activity,
    ActivityType,
    Attribution,
    BookingOutcome,
    BudgetScope,
    BudgetType,
    Child,
    LeadTimeline,
    ManagerTask,
    OutcomeStatus,
    Quote,
    QuoteReaction,
    QuoteReactionEvent,
    TaskStatus,
    TaskType,
    TripRequest,
)


def init_schema(cur: sqlite3.Cursor) -> None:
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS crm_trip_requests (
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
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS crm_quotes (
            quote_id TEXT PRIMARY KEY,
            request_id TEXT NOT NULL,
            hotel TEXT NOT NULL,
            operator TEXT,
            carrier TEXT,
            meal_plan TEXT,
            price_amount INTEGER NOT NULL,
            currency TEXT NOT NULL,
            calculated_at INTEGER NOT NULL,
            reaction TEXT NOT NULL
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS crm_quote_reactions (
            event_id TEXT PRIMARY KEY,
            quote_id TEXT NOT NULL,
            request_id TEXT NOT NULL,
            reaction TEXT NOT NULL,
            note TEXT,
            created_at INTEGER NOT NULL
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS crm_activities (
            activity_id TEXT PRIMARY KEY,
            request_id TEXT NOT NULL,
            activity_type TEXT NOT NULL,
            summary TEXT,
            created_at INTEGER NOT NULL
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS crm_tasks (
            task_id TEXT PRIMARY KEY,
            request_id TEXT NOT NULL,
            task_type TEXT NOT NULL,
            due_at INTEGER NOT NULL,
            created_at INTEGER NOT NULL,
            priority INTEGER NOT NULL,
            status TEXT NOT NULL,
            note TEXT
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS crm_outcomes (
            request_id TEXT PRIMARY KEY,
            status TEXT NOT NULL,
            reason TEXT,
            decided_at INTEGER NOT NULL
        )
        """
    )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_crm_requests_source "
        "ON crm_trip_requests(source_tag, campaign)"
    )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_crm_quotes_request "
        "ON crm_quotes(request_id, calculated_at)"
    )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_crm_quote_reactions_quote "
        "ON crm_quote_reactions(quote_id, created_at)"
    )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_crm_quote_reactions_request "
        "ON crm_quote_reactions(request_id, created_at)"
    )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_crm_activities_request "
        "ON crm_activities(request_id, created_at)"
    )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_crm_tasks_due "
        "ON crm_tasks(status, due_at, priority)"
    )


def _to_epoch(value: datetime) -> int:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return int(value.timestamp())


def _from_epoch(value: int) -> datetime:
    return datetime.fromtimestamp(int(value), tz=timezone.utc).replace(tzinfo=None)


def _request_payload(request: TripRequest) -> dict[str, Any]:
    payload = asdict(request)
    payload["budget_type"] = request.budget_type.value
    payload["budget_scope"] = request.budget_scope.value
    return payload


def _request_from_payload(payload: dict[str, Any]) -> TripRequest:
    attribution = payload.get("attribution") or {}
    return TripRequest(
        request_id=str(payload["request_id"]),
        departure_city=str(payload["departure_city"]),
        adults=int(payload["adults"]),
        children=tuple(Child(int(item["age"])) for item in payload.get("children") or ()),
        departure_airport=str(payload.get("departure_airport") or ""),
        dates_text=str(payload.get("dates_text") or ""),
        date_from=str(payload.get("date_from") or ""),
        date_to=str(payload.get("date_to") or ""),
        flexible_dates=bool(payload.get("flexible_dates")),
        nights_min=payload.get("nights_min"),
        nights_max=payload.get("nights_max"),
        budget_amount=payload.get("budget_amount"),
        budget_currency=str(payload.get("budget_currency") or "RUB"),
        budget_type=BudgetType(str(payload.get("budget_type") or BudgetType.TARGET.value)),
        budget_scope=BudgetScope(str(payload.get("budget_scope") or BudgetScope.TOTAL.value)),
        direct_only=bool(payload.get("direct_only")),
        meal_plans=tuple(str(x) for x in payload.get("meal_plans") or ()),
        primary_destination=str(payload.get("primary_destination") or ""),
        alternative_destinations=tuple(
            str(x) for x in payload.get("alternative_destinations") or ()
        ),
        resort=str(payload.get("resort") or ""),
        hotel_references=tuple(str(x) for x in payload.get("hotel_references") or ()),
        beach_preferences=tuple(str(x) for x in payload.get("beach_preferences") or ()),
        hotel_preferences=tuple(str(x) for x in payload.get("hotel_preferences") or ()),
        location_preferences=tuple(
            str(x) for x in payload.get("location_preferences") or ()
        ),
        special_wishes=tuple(str(x) for x in payload.get("special_wishes") or ()),
        trip_occasion=str(payload.get("trip_occasion") or ""),
        corporate=bool(payload.get("corporate")),
        attribution=Attribution(
            source_tag=str(attribution.get("source_tag") or ""),
            channel=str(attribution.get("channel") or ""),
            source=str(attribution.get("source") or ""),
            referrer=str(attribution.get("referrer") or ""),
            campaign=str(attribution.get("campaign") or ""),
        ),
    )


def upsert_request(
    conn: sqlite3.Connection,
    request: TripRequest,
    *,
    lead_id: int | None = None,
    now: datetime | None = None,
) -> None:
    stamp = _to_epoch(now or datetime.utcnow())
    payload = json.dumps(
        _request_payload(request),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    conn.execute(
        """
        INSERT INTO crm_trip_requests (
            request_id, lead_id, payload_json, source_tag, channel, campaign,
            created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(request_id) DO UPDATE SET
            lead_id=COALESCE(excluded.lead_id, crm_trip_requests.lead_id),
            payload_json=excluded.payload_json,
            source_tag=excluded.source_tag,
            channel=excluded.channel,
            campaign=excluded.campaign,
            updated_at=excluded.updated_at
        """,
        (
            request.request_id,
            lead_id,
            payload,
            request.attribution.source_tag,
            request.attribution.channel,
            request.attribution.campaign,
            stamp,
            stamp,
        ),
    )


def append_quote(conn: sqlite3.Connection, quote: Quote) -> None:
    conn.execute(
        """
        INSERT INTO crm_quotes (
            quote_id, request_id, hotel, operator, carrier, meal_plan,
            price_amount, currency, calculated_at, reaction
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            quote.quote_id,
            quote.request_id,
            quote.hotel,
            quote.operator,
            quote.carrier,
            quote.meal_plan,
            quote.price_amount,
            quote.currency,
            _to_epoch(quote.calculated_at),
            quote.reaction.value,
        ),
    )


def append_quote_reaction(
    conn: sqlite3.Connection,
    event: QuoteReactionEvent,
) -> None:
    quote = conn.execute(
        "SELECT request_id FROM crm_quotes WHERE quote_id = ?",
        (event.quote_id,),
    ).fetchone()
    if quote is None:
        raise ValueError("quote reaction references an unknown quote")
    if str(quote[0]) != event.request_id:
        raise ValueError("quote reaction request_id does not match quote")

    conn.execute(
        """
        INSERT INTO crm_quote_reactions (
            event_id, quote_id, request_id, reaction, note, created_at
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            event.event_id,
            event.quote_id,
            event.request_id,
            event.reaction.value,
            event.note,
            _to_epoch(event.created_at),
        ),
    )


def append_activity(conn: sqlite3.Connection, activity: Activity) -> None:
    conn.execute(
        """
        INSERT INTO crm_activities (
            activity_id, request_id, activity_type, summary, created_at
        ) VALUES (?, ?, ?, ?, ?)
        """,
        (
            activity.activity_id,
            activity.request_id,
            activity.type.value,
            activity.summary,
            _to_epoch(activity.created_at),
        ),
    )


def upsert_task(conn: sqlite3.Connection, task: ManagerTask) -> None:
    conn.execute(
        """
        INSERT INTO crm_tasks (
            task_id, request_id, task_type, due_at, created_at, priority, status, note
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(task_id) DO UPDATE SET
            due_at=excluded.due_at,
            priority=excluded.priority,
            status=excluded.status,
            note=excluded.note
        """,
        (
            task.task_id,
            task.request_id,
            task.type.value,
            _to_epoch(task.due_at),
            _to_epoch(task.created_at),
            task.priority,
            task.status.value,
            task.note,
        ),
    )


def set_outcome(
    conn: sqlite3.Connection,
    request_id: str,
    outcome: BookingOutcome,
) -> None:
    conn.execute(
        """
        INSERT INTO crm_outcomes (request_id, status, reason, decided_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(request_id) DO UPDATE SET
            status=excluded.status,
            reason=excluded.reason,
            decided_at=excluded.decided_at
        """,
        (
            request_id,
            outcome.status.value,
            outcome.reason,
            _to_epoch(outcome.decided_at),
        ),
    )


def load_timeline(conn: sqlite3.Connection, request_id: str) -> LeadTimeline | None:
    row = conn.execute(
        "SELECT payload_json FROM crm_trip_requests WHERE request_id = ?",
        (request_id,),
    ).fetchone()
    if row is None:
        return None

    request = _request_from_payload(json.loads(row[0]))

    quotes = tuple(
        Quote(
            quote_id=r[0],
            request_id=r[1],
            hotel=r[2],
            operator=r[3] or "",
            carrier=r[4] or "",
            meal_plan=r[5] or "",
            price_amount=int(r[6]),
            currency=r[7],
            calculated_at=_from_epoch(r[8]),
            reaction=QuoteReaction(r[9]),
        )
        for r in conn.execute(
            """
            SELECT quote_id, request_id, hotel, operator, carrier, meal_plan,
                   price_amount, currency, calculated_at, reaction
            FROM crm_quotes
            WHERE request_id = ?
            ORDER BY calculated_at, quote_id
            """,
            (request_id,),
        ).fetchall()
    )

    quote_reactions = tuple(
        QuoteReactionEvent(
            event_id=r[0],
            quote_id=r[1],
            request_id=r[2],
            reaction=QuoteReaction(r[3]),
            note=r[4] or "",
            created_at=_from_epoch(r[5]),
        )
        for r in conn.execute(
            """
            SELECT event_id, quote_id, request_id, reaction, note, created_at
            FROM crm_quote_reactions
            WHERE request_id = ?
            ORDER BY created_at, event_id
            """,
            (request_id,),
        ).fetchall()
    )

    activities = tuple(
        Activity(
            activity_id=r[0],
            request_id=r[1],
            type=ActivityType(r[2]),
            summary=r[3] or "",
            created_at=_from_epoch(r[4]),
        )
        for r in conn.execute(
            """
            SELECT activity_id, request_id, activity_type, summary, created_at
            FROM crm_activities
            WHERE request_id = ?
            ORDER BY created_at, activity_id
            """,
            (request_id,),
        ).fetchall()
    )

    tasks = tuple(
        ManagerTask(
            task_id=r[0],
            request_id=r[1],
            type=TaskType(r[2]),
            due_at=_from_epoch(r[3]),
            created_at=_from_epoch(r[4]),
            priority=int(r[5]),
            status=TaskStatus(r[6]),
            note=r[7] or "",
        )
        for r in conn.execute(
            """
            SELECT task_id, request_id, task_type, due_at, created_at,
                   priority, status, note
            FROM crm_tasks
            WHERE request_id = ?
            ORDER BY created_at, task_id
            """,
            (request_id,),
        ).fetchall()
    )

    outcome_row = conn.execute(
        "SELECT status, reason, decided_at FROM crm_outcomes WHERE request_id = ?",
        (request_id,),
    ).fetchone()
    outcome = None
    if outcome_row is not None:
        outcome = BookingOutcome(
            status=OutcomeStatus(outcome_row[0]),
            reason=outcome_row[1] or "",
            decided_at=_from_epoch(outcome_row[2]),
        )

    return LeadTimeline(
        request=request,
        quotes=quotes,
        quote_reactions=quote_reactions,
        activities=activities,
        tasks=tasks,
        outcome=outcome,
    )


def ensure_initial_task(
    conn: sqlite3.Connection,
    request_id: str,
    now: datetime,
) -> str:
    """Create the default manager action once for a newly mirrored lead."""

    task_id = f"{request_id}:build-selection"
    stamp = _to_epoch(now)
    conn.execute(
        """
        INSERT OR IGNORE INTO crm_tasks (
            task_id, request_id, task_type, due_at, created_at, priority, status, note
        ) VALUES (?, ?, ?, ?, ?, 1, ?, ?)
        """,
        (
            task_id,
            request_id,
            TaskType.BUILD_SELECTION.value,
            stamp,
            stamp,
            TaskStatus.TODO.value,
            "Сделать первичный подбор и отправить варианты",
        ),
    )
    return task_id


def set_task_status(
    conn: sqlite3.Connection,
    task_id: str,
    status: TaskStatus,
) -> bool:
    cur = conn.execute(
        "UPDATE crm_tasks SET status = ? WHERE task_id = ?",
        (status.value, task_id),
    )
    return bool(cur.rowcount)


def due_tasks(conn: sqlite3.Connection, now: datetime) -> list[ManagerTask]:
    rows = conn.execute(
        """
        SELECT task_id, request_id, task_type, due_at, created_at,
               priority, status, note
        FROM crm_tasks
        WHERE status = ? AND due_at <= ?
        ORDER BY priority, due_at, created_at
        """,
        (TaskStatus.TODO.value, _to_epoch(now)),
    ).fetchall()
    return [
        ManagerTask(
            task_id=r[0],
            request_id=r[1],
            type=TaskType(r[2]),
            due_at=_from_epoch(r[3]),
            created_at=_from_epoch(r[4]),
            priority=int(r[5]),
            status=TaskStatus(r[6]),
            note=r[7] or "",
        )
        for r in rows
    ]


def delete_for_lead_ids(
    conn: sqlite3.Connection,
    lead_ids: list[int],
    *,
    channel: str | None = None,
) -> int:
    """Erase CRM mirrors tied to canonical lead rows.

    lead IDs are only unique inside their source table. Channel filtering keeps
    a Telegram lead #12 from deleting an unrelated website/VK lead #12.
    """

    ids = [int(value) for value in lead_ids]
    if not ids:
        return 0
    placeholders = ",".join("?" for _ in ids)
    params: list[Any] = list(ids)
    where = f"lead_id IN ({placeholders})"
    if channel:
        where += " AND channel = ?"
        params.append(str(channel))
    request_rows = conn.execute(
        f"SELECT request_id FROM crm_trip_requests WHERE {where}",
        params,
    ).fetchall()
    request_ids = [str(row[0]) for row in request_rows]
    if not request_ids:
        return 0

    req_placeholders = ",".join("?" for _ in request_ids)
    for table in (
        "crm_quote_reactions",
        "crm_quotes",
        "crm_activities",
        "crm_tasks",
        "crm_outcomes",
    ):
        conn.execute(
            f"DELETE FROM {table} WHERE request_id IN ({req_placeholders})",
            request_ids,
        )
    conn.execute(
        f"DELETE FROM crm_trip_requests WHERE request_id IN ({req_placeholders})",
        request_ids,
    )
    return len(request_ids)
