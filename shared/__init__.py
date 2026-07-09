"""Shared logic for Telegram (bot.py) and VK (vk_bot.py) TurBot services."""

from shared.validation import validate_budget, validate_people, validate_phone
from shared.templates import (
    AI_FALLBACK_MESSAGE,
    TEMPLATE_INTROS,
    TEMPLATE_PACKING,
    template_selection,
)
from shared.dates import MONTHS_RU, parse_russian_dates
from shared.privacy import consent_text, privacy_text
from shared.constants import (
    STATE_BUDGET,
    STATE_CONSENT,
    STATE_DATES,
    STATE_DESTINATION,
    STATE_PEOPLE,
    STATE_PHONE,
    PEOPLE_OPTIONS,
    BACK_BUTTON_TEXT,
    CANCEL_BUTTON_TEXT,
    CONSENT_YES_TEXT,
    CONSENT_NO_TEXT,
)

__all__ = [
    "validate_phone",
    "validate_people",
    "validate_budget",
    "AI_FALLBACK_MESSAGE",
    "TEMPLATE_INTROS",
    "TEMPLATE_PACKING",
    "template_selection",
    "MONTHS_RU",
    "parse_russian_dates",
    "consent_text",
    "privacy_text",
    "STATE_CONSENT",
    "STATE_DESTINATION",
    "STATE_DATES",
    "STATE_PEOPLE",
    "STATE_BUDGET",
    "STATE_PHONE",
    "PEOPLE_OPTIONS",
    "BACK_BUTTON_TEXT",
    "CANCEL_BUTTON_TEXT",
    "CONSENT_YES_TEXT",
    "CONSENT_NO_TEXT",
]
