from datetime import datetime, timedelta

import pytest

from shared.travel_crm import (
    Attribution,
    BudgetType,
    Child,
    LeadTimeline,
    ManagerTask,
    Quote,
    QuoteReaction,
    TaskStatus,
    TaskType,
    TripRequest,
    tasks_due_today,
    timeline_to_dict,
)


def _request() -> TripRequest:
    return TripRequest(
        request_id="lead_demo_001",
        departure_city="Москва",
        departure_airport="SVO",
        adults=2,
        children=(Child(5), Child(9)),
        date_from="2027-01-10",
        date_to="2027-02-20",
        flexible_dates=True,
        nights_min=9,
        nights_max=12,
        budget_amount=270000,
        budget_currency="RUB",
        budget_type=BudgetType.MAX,
        meal_plans=("AI", "HB"),
        primary_destination="Вьетнам",
        alternative_destinations=("Таиланд", "Шри-Ланка"),
        hotel_references=("Synthetic Family Resort 5*",),
        beach_preferences=("песчаный пляж",),
        hotel_preferences=("большая территория",),
        location_preferences=("можно гулять пешком",),
        special_wishes=("прямой рейс предпочтителен",),
        attribution=Attribution(
            source_tag="video_dream",
            channel="telegram",
            campaign="winter_2027",
        ),
    )


def test_real_agent_request_keeps_each_child_age_and_attribution():
    request = _request()

    assert [child.age for child in request.children] == [5, 9]
    assert request.budget_type is BudgetType.MAX
    assert request.attribution.source_tag == "video_dream"
    assert request.alternative_destinations == ("Таиланд", "Шри-Ланка")


def test_quote_history_is_append_only():
    request = _request()
    timeline = LeadTimeline(request=request)
    first = Quote(
        quote_id="q1",
        request_id=request.request_id,
        hotel="Synthetic Resort A",
        operator="Demo Operator",
        price_amount=210000,
        reaction=QuoteReaction.TOO_EXPENSIVE,
    )
    second = Quote(
        quote_id="q2",
        request_id=request.request_id,
        hotel="Synthetic Resort B",
        operator="Demo Operator",
        price_amount=195000,
        reaction=QuoteReaction.THINKING,
    )

    after_first = timeline.with_quote(first)
    after_second = after_first.with_quote(second)

    assert timeline.quotes == ()
    assert [quote.quote_id for quote in after_second.quotes] == ["q1", "q2"]
    assert [quote.price_amount for quote in after_second.quotes] == [210000, 195000]


def test_manager_queue_returns_only_due_pending_tasks_in_priority_order():
    now = datetime(2026, 9, 21, 9, 0, 0)
    tasks = (
        ManagerTask(
            task_id="t1",
            request_id="lead_demo_001",
            type=TaskType.CALL_BACK,
            due_at=now - timedelta(hours=1),
            created_at=now - timedelta(days=1),
            priority=2,
        ),
        ManagerTask(
            task_id="t2",
            request_id="lead_demo_001",
            type=TaskType.SEND_OPTIONS,
            due_at=now - timedelta(hours=2),
            created_at=now - timedelta(days=1),
            priority=1,
        ),
        ManagerTask(
            task_id="t3",
            request_id="lead_demo_001",
            type=TaskType.VISA,
            due_at=now + timedelta(hours=1),
            created_at=now,
            priority=1,
        ),
        ManagerTask(
            task_id="t4",
            request_id="lead_demo_001",
            type=TaskType.DOCUMENTS,
            due_at=now - timedelta(hours=3),
            created_at=now - timedelta(days=1),
            priority=1,
            status=TaskStatus.DONE,
        ),
    )

    due = tasks_due_today(tasks, now)

    assert [task.task_id for task in due] == ["t2", "t1"]


def test_serialization_preserves_source_tag_and_enum_values():
    timeline = LeadTimeline(request=_request())

    payload = timeline_to_dict(timeline)

    assert payload["request"]["attribution"]["source_tag"] == "video_dream"
    assert payload["request"]["budget_type"] == "max"
    assert payload["request"]["children"] == [{"age": 5}, {"age": 9}]


def test_invalid_child_age_and_night_range_are_rejected():
    with pytest.raises(ValueError):
        Child(18)

    with pytest.raises(ValueError):
        TripRequest(
            request_id="bad",
            departure_city="Москва",
            adults=2,
            nights_min=14,
            nights_max=10,
        )
