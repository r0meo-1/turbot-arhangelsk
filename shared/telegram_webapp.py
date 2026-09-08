"""Validation helpers for Telegram Mini App submissions.

Menu-button Mini Apps must not trust initDataUnsafe from JavaScript. The browser
sends Telegram.WebApp.initData to our backend; the backend verifies the HMAC
with the bot token and only then accepts the trip request.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from datetime import date
from typing import Any, Dict
from urllib.parse import parse_qsl


class MiniAppValidationError(ValueError):
    """Raised when Telegram identity or the submitted trip payload is invalid."""


def validate_init_data(init_data: str, bot_token: str, *, max_age: int = 3600) -> Dict[str, Any]:
    """Validate Telegram WebApp initData and return the decoded user object.

    Implements Telegram's bot-token HMAC validation flow. ``max_age`` limits
    replay of an otherwise valid signed payload; set it to 0 to disable the age
    check in deterministic tests only.
    """
    if not isinstance(init_data, str) or not init_data or len(init_data) > 8192:
        raise MiniAppValidationError("initData is missing or too large")
    if not bot_token:
        raise MiniAppValidationError("bot token is not configured")

    pairs = dict(parse_qsl(init_data, keep_blank_values=True, strict_parsing=False))
    received_hash = pairs.pop("hash", "")
    if not received_hash or len(received_hash) != 64:
        raise MiniAppValidationError("initData hash is missing")

    data_check_string = "\n".join(f"{key}={pairs[key]}" for key in sorted(pairs))
    secret_key = hmac.new(b"WebAppData", bot_token.encode("utf-8"), hashlib.sha256).digest()
    expected_hash = hmac.new(secret_key, data_check_string.encode("utf-8"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected_hash, received_hash):
        raise MiniAppValidationError("initData signature is invalid")

    try:
        auth_date = int(pairs.get("auth_date", "0"))
    except (TypeError, ValueError) as exc:
        raise MiniAppValidationError("auth_date is invalid") from exc
    now = int(time.time())
    if max_age and (auth_date <= 0 or auth_date > now + 60 or now - auth_date > max_age):
        raise MiniAppValidationError("initData is expired")

    try:
        user = json.loads(pairs.get("user", "{}"))
    except json.JSONDecodeError as exc:
        raise MiniAppValidationError("user data is invalid") from exc
    if not isinstance(user, dict):
        raise MiniAppValidationError("user data is invalid")
    try:
        user_id = int(user.get("id"))
    except (TypeError, ValueError) as exc:
        raise MiniAppValidationError("user id is missing") from exc
    if user_id <= 0:
        raise MiniAppValidationError("user id is invalid")
    user["id"] = user_id
    return user


def _clean_text(payload: Dict[str, Any], key: str, *, max_len: int = 100) -> str:
    value = payload.get(key)
    if not isinstance(value, str):
        raise MiniAppValidationError(f"{key} must be text")
    value = value.strip()
    if not value or len(value) > max_len:
        raise MiniAppValidationError(f"{key} is invalid")
    return value


def _bounded_int(payload: Dict[str, Any], key: str, minimum: int, maximum: int) -> int:
    value = payload.get(key)
    if isinstance(value, bool):
        raise MiniAppValidationError(f"{key} is invalid")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise MiniAppValidationError(f"{key} is invalid") from exc
    if parsed < minimum or parsed > maximum:
        raise MiniAppValidationError(f"{key} is out of range")
    return parsed


def validate_trip_request(payload: Any) -> Dict[str, Any]:
    """Validate and normalise the Mini App trip-request payload."""
    if not isinstance(payload, dict):
        raise MiniAppValidationError("payload must be an object")
    if payload.get("type") != "trip_request" or payload.get("version") != 2:
        raise MiniAppValidationError("unsupported payload version")
    if payload.get("consent") is not True:
        raise MiniAppValidationError("consent is required")

    destination = _clean_text(payload, "destination")
    departure = _clean_text(payload, "departure")
    date_raw = _clean_text(payload, "date", max_len=10)
    try:
        departure_date = date.fromisoformat(date_raw)
    except ValueError as exc:
        raise MiniAppValidationError("date is invalid") from exc
    if departure_date < date.today():
        raise MiniAppValidationError("date is in the past")

    nights = _bounded_int(payload, "nights", 3, 30)
    adults = _bounded_int(payload, "adults", 1, 8)
    children = _bounded_int(payload, "children", 0, 6)
    budget = _bounded_int(payload, "budgetMaxRub", 100_000, 600_000)

    raw_ages = payload.get("childrenAges", [])
    if not isinstance(raw_ages, list) or len(raw_ages) != children:
        raise MiniAppValidationError("children ages do not match children count")
    ages = []
    for raw_age in raw_ages:
        if isinstance(raw_age, bool):
            raise MiniAppValidationError("child age is invalid")
        try:
            age = int(raw_age)
        except (TypeError, ValueError) as exc:
            raise MiniAppValidationError("child age is invalid") from exc
        if age < 0 or age > 17:
            raise MiniAppValidationError("child age is out of range")
        ages.append(age)

    return {
        "destination": destination,
        "origin": departure,
        "dates": date_raw,
        "nights": nights,
        "dates_are_trip": False,
        "people": str(adults),
        "kids_ages": ages,
        "budget": budget,
        "budget_open_ended": False,
        "direct_only": payload.get("directOnly") is True,
        "source": "telegram_mini_app",
    }
