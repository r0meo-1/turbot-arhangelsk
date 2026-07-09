"""Parse free-text Russian date ranges for MDT flight dates."""

from __future__ import annotations

import re
import time
from typing import Dict, Optional, Tuple

# Longer prefixes first to avoid false matches (e.g. "март" before "ма").
MONTHS_RU: Dict[str, int] = {
    "январ": 1,
    "феврал": 2,
    "март": 3,
    "апрел": 4,
    "ма": 5,
    "июн": 6,
    "июл": 7,
    "август": 8,
    "сентябр": 9,
    "октябр": 10,
    "ноябр": 11,
    "декабр": 12,
}


def parse_russian_dates(text: str) -> Tuple[Optional[str], Optional[str]]:
    """Parse ranges like "15-22 июня" into (from, to) YYYY-MM-DD.

    Handles "15-22 июня", "15-22 июня 2026", "15 июня - 22 июля",
    "с 1 по 15 августа". Returns (None, None) if parsing fails.
    """
    if not text:
        return None, None

    now = time.localtime()
    current_year = now.tm_year
    current_month = now.tm_mon
    current_day = now.tm_mday

    def _month_from_text(s: str) -> Optional[int]:
        for prefix, month_num in MONTHS_RU.items():
            if prefix in s.lower():
                return month_num
        return None

    def _to_ymd(day: int, month: int, year: int) -> str:
        return f"{year:04d}-{month:02d}-{day:02d}"

    year_match = re.search(r"\b(20\d{2})\b", text)
    year = int(year_match.group(1)) if year_match else current_year

    parts = re.split(r"\s*(?:-|–|—|\bпо\b)\s*", text)
    parts = [p.strip() for p in parts if p.strip()]
    if len(parts) < 2:
        return None, None

    def _parse_part(s: str) -> Optional[Tuple[int, int]]:
        day_match = re.search(r"\b(\d{1,2})\b", s)
        if not day_match:
            return None
        day = int(day_match.group(1))
        month = _month_from_text(s)
        if month is None:
            return None
        return (day, month)

    from_parsed = _parse_part(parts[0])
    to_parsed = _parse_part(parts[1])

    # "15-22 июня" — month only on the second part.
    if from_parsed is None and to_parsed is not None:
        day_match = re.search(r"\b(\d{1,2})\b", parts[0])
        if day_match:
            from_parsed = (int(day_match.group(1)), to_parsed[1])

    if from_parsed is None or to_parsed is None:
        return None, None

    from_day, from_month = from_parsed
    to_day, to_month = to_parsed

    if not year_match:
        if from_month < current_month or (
            from_month == current_month and from_day < current_day
        ):
            year = current_year + 1

    from_date = _to_ymd(from_day, from_month, year)
    to_date = _to_ymd(to_day, to_month, year)
    if to_month < from_month:
        to_date = _to_ymd(to_day, to_month, year + 1)

    return from_date, to_date
