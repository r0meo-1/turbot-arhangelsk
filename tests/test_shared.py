"""Unit tests for shared package (validators, dates, templates, MDT helpers)."""

from shared.validation import validate_phone, validate_people, validate_budget, parse_kids_ages
import pytest
from shared.templates import template_selection
from shared.dates import parse_russian_dates
from shared.privacy import consent_text, privacy_text
from shared.mdt import (
    MDTSettings,
    parse_country_list,
    match_country_id,
    extract_id,
    dispatch_lead,
    create_preorder,
    create_lead,
)


def test_validate_phone():
    assert validate_phone("+79161234567") == (True, "+79161234567")
    assert validate_phone("89161234567") == (True, "+79161234567")
    assert validate_phone("abc") == (False, None)


def test_validate_people_and_budget():
    assert validate_people("5+") == (True, "5+")
    assert validate_people("0") == (False, None)
    assert validate_budget("60 000") == (True, 60000)
    assert validate_budget("0") == (False, None)


@pytest.mark.parametrize("text", [
    "-2", "+2", "2.5", "2 и 3", "2-3", "abc2", "2 взрослых 1 ребенок", "51", "", None, 2,
])
def test_people_rejects_ambiguous_counts(text):
    assert validate_people(text) == (False, None)


@pytest.mark.parametrize("text,expected", [
    (" 2 ", "2"),
    ("2 человека", "2"),
    ("3 взрослых", "3"),
    ("2 чел.", "2"),
    ("50", "50"),
    ("5+", "5+"),
])
def test_people_accepts_one_whole_count(text, expected):
    assert validate_people(text) == (True, expected)


@pytest.mark.parametrize("text", [
    "100", "-5", "5.5", "5, неизвестно", "18", "216 месяцев", "8 месяцев 4 года", None, 5, "5," * 11,
])
def test_child_ages_rejects_invalid_or_ambiguous_values(text):
    ok, ages, problem = parse_kids_ages(text)
    assert not ok
    assert ages == []
    assert problem


@pytest.mark.parametrize("text,expected", [
    ("5, 9", [5, 9]),
    ("5 и 9", [5, 9]),
    ("5 9", [5, 9]),
    ("5 лет, 9 лет", [5, 9]),
    ("до года и 4", [0, 4]),
    ("8 месяцев и 4", [0, 4]),
    ("18 месяцев", [1]),
    ("24 мес.", [2]),
    ("0, 5", [0, 5]),
    ("нет детей", []),
    ("0", []),
])
def test_child_ages_preserves_whole_years_and_infants(text, expected):
    assert parse_kids_ages(text) == (True, expected, "")


@pytest.mark.parametrize("text", [
    "7+916123456", "++79161234567", "+7916123456", "abc9161234567", "7.9161234567", None, 79161234567,
])
def test_phone_rejects_malformed_numbers(text):
    assert validate_phone(text) == (False, None)


@pytest.mark.parametrize("text", ["+7 (916) 123-45-67", "8 (916) 123 45 67", "9161234567"])
def test_phone_preserves_formatted_numbers(text):
    assert validate_phone(text) == (True, "+79161234567")


def test_phone_keeps_leading_eight_in_ten_digit_local_number():
    assert validate_phone("8001234567") == (True, "+78001234567")


@pytest.mark.parametrize("text,expected", [
    ("грудничок 6 месяцев", [0]),
    ("младенцу 8 месяцев", [0]),
    ("МЛАДЕНЕЦ 18 мес.", [1]),
    ("грудному ребенку 6 месяцев и 4 года", [0, 4]),
])
def test_child_ages_accepts_complete_infant_descriptions(text, expected):
    assert parse_kids_ages(text) == (True, expected, "")


@pytest.mark.parametrize("text", [
    "грудничок -6 месяцев", "младенцу 8.5 месяцев",
    "младенцу 8 месяцев 4 года", "младенцу 216 месяцев",
])
def test_child_ages_rejects_invalid_infant_descriptions(text):
    ok, ages, problem = parse_kids_ages(text)
    assert not ok and ages == [] and problem


@pytest.mark.parametrize("dash", ["\u2010", "\u2011", "\u2012", "\u2013", "\u2014"])
def test_phone_accepts_typographic_dashes(dash):
    assert validate_phone(f"+7\u00a0916{dash}123{dash}45{dash}67") == (True, "+79161234567")
    assert validate_phone(f"+7{dash}916{dash}123{dash}45{dash}67") == (True, "+79161234567")
    assert validate_phone(f"7{dash}+9161234567") == (False, None)
    assert validate_phone(f"+7{dash}916123456") == (False, None)


@pytest.mark.parametrize("text,expected", [
    ("2 взрослых человека", "2"), ("1 взрослый человек", "1"),
    ("3 взр. чел.", "3"), ("50 взрослых человек", "50"),
])
def test_people_accepts_combined_count_labels(text, expected):
    assert validate_people(text) == (True, expected)


@pytest.mark.parametrize("text", [
    "2 взрослых человека 1 ребенок", "2.5 взрослых человека",
    "51 взрослый человек", "2 взрослых взрослых", "2 человека человека",
])
def test_people_rejects_ambiguous_combined_count_labels(text):
    assert validate_people(text) == (False, None)


@pytest.mark.parametrize("text,expected", [
    ("100000", 100000), ("100 000 ₽", 100000), ("100\u00a0000 руб.", 100000),
    ("100\u202f000 рублей", 100000), ("до 120 тыс.", 120000), ("120к", 120000),
    ("120 K", 120000), ("120 тысяч рублей", 120000),
])
def test_budget_accepts_one_amount(text, expected):
    assert validate_budget(text) == (True, expected)


@pytest.mark.parametrize("text", [
    "100000–120000", "100000-120000", "100000—120000", "от 100000 до 120000",
    "100/120 тыс", "100,120", "100.50", "-60000", "+60000", "0", "0 тыс",
    "100000 на 2 человека", "1e5", "abc123", "60 00", "120000 USD", "", None,
    "9" * 30, "9" * 101,
])
def test_budget_rejects_ambiguous_or_invalid_amount(text):
    assert validate_budget(text) == (False, None)


def test_template_selection():
    text = template_selection("Турция", "15-22 июня", "2", "60000")
    assert "Турция" in text
    assert "Заявка у менеджера" in text


def test_parse_russian_dates_basic():
    # Explicit year so the test is stable regardless of current date.
    frm, to = parse_russian_dates("15-22 июня 2030")
    assert frm == "2030-06-15"
    assert to == "2030-06-22"


def test_parse_russian_dates_cross_month():
    frm, to = parse_russian_dates("28 июня - 5 июля 2030")
    assert frm == "2030-06-28"
    assert to == "2030-07-05"


def test_privacy_and_consent():
    c = consent_text("ООО Тест", privacy_policy_url="https://example.com/p")
    assert "ООО Тест" in c
    assert "https://example.com/p" in c
    p = privacy_text("ООО Тест", "Telegram", erase_hint="команда /delete")
    assert "Telegram" in p
    assert "/delete" in p


def test_parse_country_list_and_match():
    cache = parse_country_list({"1": "Турция", "2": "Египет"})
    assert cache["турция"] == 1
    assert match_country_id(cache, "Египет, Хургада") == 2
    assert match_country_id(cache, "Неизвестно") == 0


def test_extract_id():
    assert extract_id({"data": {"id": 42}}, "id") == 42
    assert extract_id({"data": 7}) == 7
    assert extract_id(None) is None


def test_dispatch_lead_disabled():
    calls = []
    settings = MDTSettings(enabled=False)
    dispatch_lead(
        settings, 1, {"destination": "Турция"}, "+7900", "Иван", {},
        request_fn=lambda m, p: calls.append((m, p)) or {"id": 1},
    )
    assert calls == []


def test_dispatch_lead_mode():
    calls = []

    def req(method, params):
        calls.append(method)
        return {"id": 100}

    settings = MDTSettings(
        enabled=True,
        mode="lead",
        source="Test",
        name_prefix="Telegram",
    )
    dispatch_lead(
        settings,
        1,
        {"destination": "Египет", "dates": "1-7 июля", "people": "2", "budget": 50000},
        "+79001112233",
        "Анна",
        {},
        request_fn=req,
    )
    assert calls == ["add-lead"]


def test_create_preorder_flow():
    calls = []

    def req(method, params):
        calls.append(method)
        if method == "add-tourist-temp":
            return {"id": 10}
        if method == "create-preorder":
            return {"id": 20}
        return None

    settings = MDTSettings(enabled=True, mode="preorder", tourist_tags="Telegram Bot", manager_ids=[5])
    preorder_id, tourist_id = create_preorder(
        settings,
        5,
        {"destination": "Турция", "dates": "10-17 июля 2030", "people": "2", "budget": 60000},
        "+7900",
        "Анна",
        {"турция": 3},
        request_fn=req,
    )
    assert preorder_id == 20
    assert tourist_id == 10
    assert calls == ["add-tourist-temp", "create-preorder"]


def test_create_preorder_auto_assigns_sole_active_manager():
    calls = []
    payloads = {}

    def req(method, params):
        calls.append(method)
        payloads[method] = params
        if method == "get-manager-list":
            return {
                "data": [
                    {"id": "17", "dismissed": 0, "office_id": 1},
                    {"id": "99", "dismissed": 1, "office_id": 1},
                ]
            }
        if method == "add-tourist-temp":
            return {"id": 10}
        if method == "create-preorder":
            return {"data": {"preorder_id": 20}}
        return None

    settings = MDTSettings(
        enabled=True, mode="preorder", source="VK Bot", name_prefix="VK", tourist_tags="VK Bot"
    )
    preorder_id, tourist_id = create_preorder(
        settings,
        424242,
        {
            "destination": "Таиланд",
            "origin": "Архангельск",
            "dates": "2026-10-15",
            "nights": "10",
            "people": "2",
            "kids": 0,
            "kids_ages": [],
            "budget": 270000,
            "budget_scope": "total",
            "selected_tour": {
                "hotel": "Mandarava Resort",
                "date": "2026-10-15",
                "nights": 10,
                "meal": "BB",
                "price": 155000,
                "tour_id": "th-6",
            },
        },
        "VK (чат id 424242) · Тест",
        "Тест VK",
        {"таиланд": 7},
        request_fn=req,
    )

    assert preorder_id == 20
    assert tourist_id == 10
    assert calls == ["get-manager-list", "add-tourist-temp", "create-preorder"]
    assert payloads["add-tourist-temp"]["manager_id"] == 17
    preorder = payloads["create-preorder"]
    assert preorder["preorder_manager_id"] == 17
    assert preorder["nights_from"] == 10
    assert preorder["nights_to"] == 10
    assert preorder["link"] == "https://vk.com/id424242"
    assert "Вылет: Архангельск" in preorder["comment"]
    assert "Источник: VK Bot" in preorder["comment"]
    assert "Mandarava Resort" in preorder["comment"]
    assert "ID th-6" in preorder["comment"]


def test_create_preorder_does_not_guess_between_multiple_active_managers():
    payloads = {}

    def req(method, params):
        payloads.setdefault(method, []).append(params)
        if method == "get-manager-list":
            return {"data": [{"id": 17, "dismissed": 0}, {"id": 18, "dismissed": 0}]}
        if method == "add-tourist-temp":
            return {"id": 10}
        if method == "create-preorder":
            return {"id": 20}
        return None

    settings = MDTSettings(enabled=True, mode="preorder", name_prefix="VK")
    preorder_id, _ = create_preorder(
        settings, 424242, {"destination": "Таиланд", "people": "2"},
        "VK chat", "Тест", {"таиланд": 7}, request_fn=req
    )
    assert preorder_id == 20
    assert "manager_id" not in payloads["add-tourist-temp"][0]
    assert "preorder_manager_id" not in payloads["create-preorder"][0]


def test_create_lead_includes_selected_tour():
    captured = {}

    def req(method, params):
        captured["method"] = method
        captured["params"] = params
        return {"id": 321}

    settings = MDTSettings(enabled=True, mode="lead", source="VK")
    ok = create_lead(
        settings,
        424242,
        {
            "destination": "Таиланд",
            "dates": "2026-10-15",
            "nights": "10",
            "people": "2",
            "budget": 270000,
            "budget_scope": "total",
            "selected_tour": {
                "hotel": "Mandarava Resort & Spa Karon Beach 5★",
                "date": "2026-10-29",
                "nights": 10,
                "meal": "Завтраки",
                "price": 155000,
                "tour_id": "th-6",
            },
        },
        "+70000000000",
        "Тест VK",
        request_fn=req,
    )

    assert ok is True
    assert captured["method"] == "add-lead"
    fields = {field["name"]: field["values"][0] for field in captured["params"]["fields"]}
    assert "Mandarava Resort & Spa Karon Beach 5★" in fields["Выбранный тур"]
    assert "155000 ₽" in fields["Выбранный тур"]
    assert "ID th-6" in fields["Выбранный тур"]



def test_create_lead_rejects_error_json_without_id():
    settings = MDTSettings(enabled=True, mode="lead", source="VK", name_prefix="VK")

    ok = create_lead(
        settings,
        424242,
        {"destination": "Шри-Ланка", "_mdt_delivery_key": "vk-lead-35"},
        "VK (чат id 424242) · Тест",
        "Тест VK",
        request_fn=lambda method, params: {"error": "validation_failed"},
    )

    assert ok is False


def test_create_lead_keeps_vk_contact_for_mdt_and_metadata():
    captured = {}

    def req(method, params):
        captured["method"] = method
        captured["params"] = params
        return {"data": {"id": 654}}

    settings = MDTSettings(enabled=True, mode="lead", source="VK Bot", name_prefix="VK")
    ok = create_lead(
        settings,
        424242,
        {
            "destination": "Шри-Ланка",
            "origin": "Архангельск",
            "dates": "2026-10-15",
            "people": "2",
            "budget": 270000,
            "_mdt_delivery_key": "vk-lead-35",
        },
        "VK (чат id 424242) · Тест",
        "Тест VK",
        request_fn=req,
    )

    assert ok is True
    assert captured["method"] == "add-lead"
    params = captured["params"]
    assert params["name"] == "Тест VK"
    assert params["source"] == "VK Bot"
    assert params["phone"] == "VK (чат id 424242) · Тест"
    fields = {field["name"]: field["values"][0] for field in params["fields"]}
    assert fields["Вылет"] == "Архангельск"
    assert params["external_lead_id"] == "vk-lead-35"
    assert params["url"] == "https://vk.com/id424242"
    assert params["content"] == "Контакт: VK (чат id 424242) · Тест"


def test_create_lead_keeps_real_phone_in_phone_field():
    captured = {}

    def req(method, params):
        captured.update(params)
        return {"id": 777}

    settings = MDTSettings(enabled=True, mode="lead", source="Test")
    ok = create_lead(
        settings,
        7,
        {"destination": "Турция"},
        "+7 (900) 123-45-67",
        "Тест",
        request_fn=req,
    )

    assert ok is True
    assert captured["phone"] == "+7 (900) 123-45-67"
    assert "content" not in captured

@pytest.mark.parametrize("text, expected", [
    ("15 января 2030", ("2030-01-15", None)),
    ("29 февраля 2032", ("2032-02-29", None)),
    ("29 февраля 2030", (None, None)),
    ("31 апреля 2030", (None, None)),
    ("0 мая 2030", (None, None)),
    ("15 января 2030 мусор", (None, None)),
])
def test_single_departure_date_without_invented_return(text, expected):
    assert parse_russian_dates(text) == expected
