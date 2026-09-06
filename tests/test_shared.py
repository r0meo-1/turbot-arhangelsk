"""Unit tests for shared package (validators, dates, templates, MDT helpers)."""

from shared.validation import validate_phone, validate_people, validate_budget
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

    settings = MDTSettings(enabled=True, mode="preorder", tourist_tags="Telegram Bot")
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
