import hashlib
import hmac
import json
import time
from urllib.parse import urlencode

import pytest

from shared.telegram_webapp import (
    MiniAppValidationError,
    validate_init_data,
    validate_trip_request,
)


BOT_TOKEN = "123456789:test_bot_token_for_unit_tests"


def _signed_init_data(*, user_id=12345, auth_date=None, token=BOT_TOKEN):
    fields = {
        "auth_date": str(auth_date or int(time.time())),
        "query_id": "AAHdF6IQAAAAAN0XohDhrOrc",
        "user": json.dumps({"id": user_id, "first_name": "Roma", "username": "tester"}, separators=(",", ":")),
    }
    check = "\n".join(f"{key}={fields[key]}" for key in sorted(fields))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return urlencode(fields)


def _payload(**overrides):
    payload = {
        "type": "trip_request",
        "version": 2,
        "destination": "Таиланд",
        "departure": "Москва",
        "date": "2099-01-15",
        "nights": 10,
        "adults": 2,
        "children": 2,
        "childrenAges": [5, 12],
        "budgetMaxRub": 270000,
        "directOnly": True,
        "consent": True,
    }
    payload.update(overrides)
    return payload


def test_validate_init_data_accepts_telegram_signature():
    user = validate_init_data(_signed_init_data(), BOT_TOKEN)
    assert user["id"] == 12345
    assert user["username"] == "tester"


def test_validate_init_data_rejects_tampering():
    init_data = _signed_init_data().replace("Roma", "Roman")
    with pytest.raises(MiniAppValidationError):
        validate_init_data(init_data, BOT_TOKEN)


def test_validate_init_data_rejects_expired_payload():
    old = int(time.time()) - 7200
    with pytest.raises(MiniAppValidationError, match="expired"):
        validate_init_data(_signed_init_data(auth_date=old), BOT_TOKEN, max_age=3600)


def test_trip_request_normalises_fields_and_keeps_child_ages():
    result = validate_trip_request(_payload())
    assert result["destination"] == "Таиланд"
    assert result["origin"] == "Москва"
    assert result["people"] == "2"
    assert result["kids_ages"] == [5, 12]
    assert result["budget"] == 270000
    assert result["direct_only"] is True


def test_trip_request_requires_exact_child_age_count():
    with pytest.raises(MiniAppValidationError, match="children ages"):
        validate_trip_request(_payload(children=2, childrenAges=[5]))


def test_trip_request_requires_consent():
    with pytest.raises(MiniAppValidationError, match="consent"):
        validate_trip_request(_payload(consent=False))
