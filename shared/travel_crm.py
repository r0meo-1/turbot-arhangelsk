"""Domain model for TurBot's real travel-agent workflow.

Built from observed agent workflow patterns, but uses no real client PII.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
from datetime import datetime
from enum import Enum
from typing import Any


class BudgetType(str, Enum):
    TARGET = "target"
    MAX = "max"
    FIXED = "fixed"


class QuoteReaction(str, Enum):
    DRAFT = "draft"
    SENT = "sent"
    VIEWED = "viewed"
    TOO_EXPENSIVE = "too_expensive"
    THINKING = "thinking"
    WANTS_ALTERNATIVE = "wants_alternative"
    ACCEPTED = "accepted"
    REJECTED = "rejected"


class ActivityType(str, Enum):
    MESSAGE_SENT = "message_sent"
    MESSAGE_RECEIVED = "message_received"
    NOTE = "note"
    CALL = "call"
    STATUS_CHANGE = "status_change"


class TaskStatus(str, Enum):
    TODO = "todo"
    DONE = "done"
    CANCELED = "canceled"


class TaskType(str, Enum):
    BUILD_SELECTION = "build_selection"
    SEND_OPTIONS = "send_options"
    CALL_BACK = "call_back"
    CHECK_PRICE = "check_price"
    FLIGHTS = "flights"
    VISA = "visa"
    DOCUMENTS = "documents"
    NEXT_CONTACT = "next_contact"


class OutcomeStatus(str, Enum):
    WON = "won"
    LOST = "lost"
    PAUSED = "paused"


@dataclass(frozen=True)
class Child:
    age: int

    def __post_init__(self) -> None:
        if not 0 <= self.age <= 17:
            raise ValueError("child age must be between 0 and 17")


@dataclass(frozen=True)
class Attribution:
    source_tag: str = ""
    channel: str = ""
    referrer: str = ""
    campaign: str = ""


@dataclass(frozen=True)
class TripRequest:
    request_id: str
    departure_city: str
    adults: int
    children: tuple[Child, ...] = ()
    departure_airport: str = ""
    date_from: str = ""
    date_to: str = ""
    flexible_dates: bool = False
    nights_min: int | None = None
    nights_max: int | None = None
    budget_amount: int | None = None
    budget_currency: str = "RUB"
    budget_type: BudgetType = BudgetType.TARGET
    meal_plans: tuple[str, ...] = ()
    primary_destination: str = ""
    alternative_destinations: tuple[str, ...] = ()
    resort: str = ""
    hotel_references: tuple[str, ...] = ()
    beach_preferences: tuple[str, ...] = ()
    hotel_preferences: tuple[str, ...] = ()
    location_preferences: tuple[str, ...] = ()
    special_wishes: tuple[str, ...] = ()
    trip_occasion: str = ""
    corporate: bool = False
    attribution: Attribution = field(default_factory=Attribution)

    def __post_init__(self) -> None:
        if self.adults < 1:
            raise ValueError("adults must be >= 1")
        if self.nights_min is not None and self.nights_min < 1:
            raise ValueError("nights_min must be >= 1")
        if self.nights_max is not None and self.nights_max < 1:
            raise ValueError("nights_max must be >= 1")
        if (
            self.nights_min is not None
            and self.nights_max is not None
            and self.nights_min > self.nights_max
        ):
            raise ValueError("nights_min cannot exceed nights_max")
        if self.budget_amount is not None and self.budget_amount <= 0:
            raise ValueError("budget_amount must be positive")
        if not self.request_id.strip():
            raise ValueError("request_id is required")
        if not self.departure_city.strip():
            raise ValueError("departure_city is required")


@dataclass(frozen=True)
class Quote:
    quote_id: str
    request_id: str
    hotel: str
    operator: str = ""
    carrier: str = ""
    meal_plan: str = ""
    price_amount: int = 0
    currency: str = "RUB"
    calculated_at: datetime = field(default_factory=datetime.utcnow)
    reaction: QuoteReaction = QuoteReaction.DRAFT

    def __post_init__(self) -> None:
        if self.price_amount < 0:
            raise ValueError("price_amount cannot be negative")
        if not self.quote_id.strip():
            raise ValueError("quote_id is required")
        if not self.request_id.strip():
            raise ValueError("request_id is required")


@dataclass(frozen=True)
class Activity:
    activity_id: str
    request_id: str
    type: ActivityType
    created_at: datetime
    summary: str = ""

    def __post_init__(self) -> None:
        if not self.activity_id.strip():
            raise ValueError("activity_id is required")
        if not self.request_id.strip():
            raise ValueError("request_id is required")


@dataclass(frozen=True)
class ManagerTask:
    task_id: str
    request_id: str
    type: TaskType
    due_at: datetime
    created_at: datetime
    priority: int = 3
    status: TaskStatus = TaskStatus.TODO
    note: str = ""

    def __post_init__(self) -> None:
        if not 1 <= self.priority <= 4:
            raise ValueError("priority must be in range 1..4")


@dataclass(frozen=True)
class BookingOutcome:
    status: OutcomeStatus
    reason: str = ""
    decided_at: datetime = field(default_factory=datetime.utcnow)


@dataclass(frozen=True)
class LeadTimeline:
    request: TripRequest
    quotes: tuple[Quote, ...] = ()
    activities: tuple[Activity, ...] = ()
    tasks: tuple[ManagerTask, ...] = ()
    outcome: BookingOutcome | None = None

    def with_quote(self, quote: Quote) -> "LeadTimeline":
        if quote.request_id != self.request.request_id:
            raise ValueError("quote belongs to another request")
        if any(item.quote_id == quote.quote_id for item in self.quotes):
            raise ValueError("quote_id already exists")
        return replace(self, quotes=(*self.quotes, quote))

    def with_activity(self, activity: Activity) -> "LeadTimeline":
        if activity.request_id != self.request.request_id:
            raise ValueError("activity belongs to another request")
        if any(item.activity_id == activity.activity_id for item in self.activities):
            raise ValueError("activity_id already exists")
        return replace(self, activities=(*self.activities, activity))

    def with_task(self, task: ManagerTask) -> "LeadTimeline":
        if task.request_id != self.request.request_id:
            raise ValueError("task belongs to another request")
        if any(item.task_id == task.task_id for item in self.tasks):
            raise ValueError("task_id already exists")
        return replace(self, tasks=(*self.tasks, task))

    def with_outcome(self, outcome: BookingOutcome) -> "LeadTimeline":
        return replace(self, outcome=outcome)


def tasks_due_today(tasks: tuple[ManagerTask, ...], now: datetime) -> list[ManagerTask]:
    """Return pending tasks due now or overdue, ordered for a manager's daily queue."""

    due = [
        task
        for task in tasks
        if task.status == TaskStatus.TODO and task.due_at <= now
    ]
    return sorted(due, key=lambda task: (task.priority, task.due_at, task.created_at))


def _json_ready(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, tuple):
        return [_json_ready(item) for item in value]
    if isinstance(value, list):
        return [_json_ready(item) for item in value]
    if isinstance(value, dict):
        return {key: _json_ready(item) for key, item in value.items()}
    return value


def timeline_to_dict(timeline: LeadTimeline) -> dict[str, Any]:
    """Serialize a timeline without mutating quote/task history."""

    return _json_ready(asdict(timeline))
