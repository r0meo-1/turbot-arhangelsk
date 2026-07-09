"""Input validation for tour-request dialog fields."""

from __future__ import annotations

import re
from typing import Optional, Tuple


def validate_phone(text: str) -> Tuple[bool, Optional[str]]:
    """Validate a Russian phone number. Returns (ok, normalised E.164-like)."""
    digits = re.sub(r"[^\d+]", "", text).lstrip("+")
    if digits.startswith("8"):
        digits = "7" + digits[1:]
    if len(digits) == 11 and digits.startswith("7"):
        return True, "+" + digits
    if len(digits) == 10:
        return True, "+7" + digits
    return False, None


def validate_people(text: str) -> Tuple[bool, Optional[str]]:
    """Validate number of travellers. Returns (ok, value_str)."""
    cleaned = text.strip()
    if cleaned == "5+":
        return True, "5+"
    try:
        value = int(re.sub(r"[^\d]", "", cleaned))
        if 1 <= value <= 50:
            return True, str(value)
    except (ValueError, TypeError):
        pass
    return False, None


def validate_budget(text: str) -> Tuple[bool, Optional[int]]:
    """Parse budget as a positive integer. Returns (ok, value)."""
    try:
        value = int(re.sub(r"[^\d]", "", text))
        if value > 0:
            return True, value
    except (ValueError, TypeError):
        pass
    return False, None
