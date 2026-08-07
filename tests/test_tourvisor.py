"""Unit tests for the Tourvisor adapter; no test touches the real API."""

from datetime import date

from shared import tourvisor


def test_exact_trip_dates_become_departure_and_nights():
    window = tourvisor.resolve_search_window(
        "15-22 сентября 2030", today=date(2030, 1, 1)
    )

    assert window == tourvisor.SearchWindow(
        date_from="2030-09-15",
        date_to="2030-09-15",
        nights_from=7,
        nights_to=7,
    )


def test_flexible_dates_stay_inside_tourvisor_limits():
    window = tourvisor.resolve_search_window("даты гибкие", today=date(2030, 1, 1))

    assert window is not None
    assert window.date_from == "2030-01-15"
    assert window.date_to == "2030-02-05"
    assert window.nights_to - window.nights_from <= 10


def test_search_maps_dialog_fields_and_returns_cheapest_unique_hotels():
    calls = []

    def request(method, path, params=None):
        calls.append((method, path, params))
        if path == "departures":
            return [{"id": 1, "name": "Москва"}]
        if path == "countries":
            return [{"id": 4, "name": "Турция"}]
        if path == "tours/search":
            return {"searchId": 77}
        if path.endswith("/status"):
            return {"progress": 100, "status": "completed"}
        if path == "tours/search/77":
            return [
                {
                    "name": "Hotel Expensive",
                    "category": 5,
                    "region": {"name": "Анталья"},
                    "tours": [{
                        "id": "b", "date": "2030-09-15", "nights": 7,
                        "price": 210000, "currency": "RUB", "roomType": "Standard",
                        "meal": {"russianName": "Всё включено"},
                        "operator": {"russianName": "Оператор Б"},
                    }],
                },
                {
                    "name": "Hotel Best",
                    "category": 4,
                    "picturelink": "https://example.test/best.jpg",
                    "subRegion": {"name": "Сиде"},
                    "tours": [
                        {
                            "id": "a2", "date": "2030-09-15", "nights": 7,
                            "price": 190000, "currency": "RUB", "roomType": "Family",
                            "meal": {"russianName": "Завтраки"},
                            "operator": {"russianName": "Оператор А"},
                        },
                        {
                            "id": "a1", "date": "2030-09-15", "nights": 7,
                            "price": 180000, "currency": "RUB", "roomType": "Standard",
                            "meal": {"russianName": "Всё включено"},
                            "operator": {"russianName": "Оператор А"},
                        },
                    ],
                },
            ]
        raise AssertionError(path)

    result = tourvisor.search_tours(
        tourvisor.TourvisorSettings(
            enabled=True, token="test", poll_interval=0, max_wait=1, max_offers=3
        ),
        session=None,  # injected request function owns transport
        info={
            "destination": "Турция",
            "origin": "Москва",
            "dates": "15-22 сентября 2030",
            "people": "2",
            "kids_ages": [5, 9],
            "budget": 70000,
        },
        request_fn=request,
        sleep_fn=lambda _: None,
    )

    assert [offer.hotel for offer in result.offers] == ["Hotel Best", "Hotel Expensive"]
    assert result.offers[0].price == 180000
    assert result.offers[0].tour_id == "a1"
    assert result.offers[0].picture_url == "https://example.test/best.jpg"
    start = next(call for call in calls if call[1] == "tours/search")
    assert start[2]["childs"] == [5, 9]
    assert start[2]["priceTo"] == 280000
    assert start[2]["nightsFrom"] == 7
    assert start[2]["nightsTo"] == 7


def test_search_rejects_party_too_large_without_api_call():
    result = tourvisor.search_tours(
        tourvisor.TourvisorSettings(enabled=True, token="test"),
        session=None,
        info={
            "destination": "Турция", "origin": "Москва",
            "dates": "15-22 сентября 2030", "people": "7",
        },
        request_fn=lambda *args: (_ for _ in ()).throw(AssertionError("must not call")),
    )

    assert not result.offers
    assert "6 взрослых" in result.error


def test_open_ended_budget_does_not_set_price_ceiling():
    captured = {}

    def request(method, path, params=None):
        if path == "departures":
            return [{"id": 1, "name": "Москва"}]
        if path == "countries":
            return [{"id": 4, "name": "Турция"}]
        if path == "tours/search":
            captured.update(params)
            return {"searchId": 77}
        if path.endswith("/status"):
            return {"progress": 100}
        return []

    tourvisor.search_tours(
        tourvisor.TourvisorSettings(
            enabled=True, token="test", poll_interval=0, max_wait=1
        ),
        session=None,
        info={
            "destination": "Турция", "origin": "Москва",
            "dates": "15-22 сентября 2030", "people": "2",
            "budget": 150000, "budget_open_ended": True,
        },
        request_fn=request,
        sleep_fn=lambda _: None,
    )

    assert "priceTo" not in captured


def test_total_budget_is_not_multiplied_by_party_size():
    seen = {}

    def request(method, path, params=None):
        if path == "departures":
            return [{"id": 1, "name": "Москва"}]
        if path == "countries":
            return [{"id": 2, "name": "Турция"}]
        if path == "tours/search":
            seen.update(params or {})
            return {"searchId": 10}
        if path.endswith("/status"):
            return {"progress": 100, "status": "completed"}
        return []

    tourvisor.search_tours(
        tourvisor.TourvisorSettings(
            enabled=True, token="test", poll_interval=0, max_wait=1
        ),
        session=None,
        info={
            "origin": "Москва", "destination": "Турция",
            "dates": "15-22 сентября 2030", "people": "2",
            "kids_ages": [5], "budget": 200000, "budget_scope": "total",
        },
        request_fn=request, sleep_fn=lambda _: None,
    )

    assert seen["priceTo"] == 200000


def test_client_message_states_total_tour_price():
    result = tourvisor.SearchResult(offers=[tourvisor.TourOffer(
        hotel="Hotel", category=4, region="Сиде", date="2030-09-15",
        nights=7, meal="Всё включено", room="Standard", operator="Алеан",
        price=180000, fuel_charge=5000,
    )])

    text = tourvisor.format_client_message(result)

    assert "180 000 ₽ + сбор 5 000 ₽ за тур" in text
    assert "на человека" not in text
