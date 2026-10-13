"""Finite monetary amounts for provider quotes and JSON-backed bot state."""

from decimal import Decimal, DecimalException
import math
from typing import Any


def money_amount(value: Any, *, allow_zero: bool = False) -> Decimal:
    if isinstance(value, bool):
        raise ValueError("Boolean is not a monetary amount")
    try:
        amount = Decimal(str(value))
    except (DecimalException, ValueError, TypeError) as exc:
        raise ValueError("Invalid monetary amount") from exc
    if not amount.is_finite() or amount < 0 or (not allow_zero and amount == 0):
        raise ValueError("Invalid monetary amount")
    return amount


def money_json(amount: Decimal) -> int | float:
    amount = money_amount(amount, allow_zero=True)
    if amount == amount.to_integral_value():
        return int(amount)
    value = float(amount)
    if not math.isfinite(value) or (amount > 0 and value == 0):
        raise ValueError("Monetary amount cannot be represented in JSON")
    return value
