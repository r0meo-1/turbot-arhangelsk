from shared.travel_crm import BudgetScope, BudgetType
from shared.travel_crm_adapter import trip_request_from_lead


def test_telegram_lead_maps_child_ages_budget_and_source_tag():
    request = trip_request_from_lead(
        lead_id=41,
        channel="telegram",
        info={
            "origin": "Москва",
            "destination": "Вьетнам",
            "dates": "январь, даты гибкие",
            "nights": 10,
            "people": "2",
            "kids_ages": [5, 9],
            "budget": 270000,
            "budget_scope": "total",
            "direct_only": True,
            "source_tag": "video_dream",
        },
    )

    assert request.request_id == "tg-lead-41"
    assert request.departure_city == "Москва"
    assert [child.age for child in request.children] == [5, 9]
    assert request.nights_min == request.nights_max == 10
    assert request.flexible_dates is True
    assert request.budget_type is BudgetType.MAX
    assert request.budget_scope is BudgetScope.TOTAL
    assert request.direct_only is True
    assert request.attribution.channel == "telegram"
    assert request.attribution.source_tag == "video_dream"


def test_vk_lead_maps_range_hotel_source_and_consultation_without_pii():
    request = trip_request_from_lead(
        lead_id=12,
        channel="vk",
        info={
            "origin": "Архангельск",
            "destination": "Турция",
            "dates": "июнь",
            "nights": "10-14",
            "people": "2",
            "kids_ages": [7],
            "budget": 180000,
            "budget_scope": "per_person",
            "hotel_query": "Synthetic Family Resort",
            "source": "VK Mini App",
            "source_tag": "summer_family",
            "vk_ref": "ad_demo_01",
            "needs_consultation": True,
        },
    )

    assert request.request_id == "vk-lead-12"
    assert (request.nights_min, request.nights_max) == (10, 14)
    assert request.hotel_references == ("Synthetic Family Resort",)
    assert request.budget_scope is BudgetScope.PER_PERSON
    assert request.special_wishes == ("нужна консультация",)
    assert request.attribution.source == "VK Mini App"
    assert request.attribution.referrer == "ad_demo_01"


def test_missing_optional_fields_degrade_without_inventing_preferences():
    request = trip_request_from_lead(
        lead_id=1,
        channel="telegram",
        info={"people": "1", "origin": "Москва"},
    )

    assert request.children == ()
    assert request.primary_destination == ""
    assert request.hotel_references == ()
    assert request.budget_amount is None
    assert request.nights_min is None


def test_invalid_channel_is_rejected():
    try:
        trip_request_from_lead(lead_id=1, channel="email", info={})
    except ValueError as exc:
        assert "channel" in str(exc)
    else:
        raise AssertionError("invalid channel must fail")
