from shared.telegram_webapp import MiniAppValidationError, validate_trip_request
from shared.vk_miniapp import validate_vk_trip


def _payload(**overrides):
    payload = {
        "type": "trip_request",
        "version": 2,
        "destination": "Таиланд",
        "departure": "Архангельск",
        "date": "2099-10-15",
        "nights": 10,
        "adults": 2,
        "children": 0,
        "childrenAges": [],
        "budgetMaxRub": 270000,
        "directOnly": True,
        "consent": True,
    }
    payload.update(overrides)
    return payload


def test_telegram_contract_defaults_legacy_budget_to_per_person():
    info = validate_trip_request(_payload())
    assert info["budget_scope"] == "per_person"
    assert info["direct_only"] is True


def test_shared_contract_accepts_explicit_budget_scope():
    assert validate_trip_request(_payload(budgetScope="total"))["budget_scope"] == "total"
    assert validate_trip_request(_payload(budgetScope="per_person"))["budget_scope"] == "per_person"


def test_shared_contract_rejects_unknown_budget_scope():
    try:
        validate_trip_request(_payload(budgetScope="mystery"))
    except MiniAppValidationError:
        pass
    else:
        raise AssertionError("unknown budgetScope must be rejected")


def test_vk_contract_keeps_legacy_total_default_but_accepts_explicit_scope():
    assert validate_vk_trip(_payload())["budget_scope"] == "total"
    assert validate_vk_trip(_payload(budgetScope="per_person"))["budget_scope"] == "per_person"
