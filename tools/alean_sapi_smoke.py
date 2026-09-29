#!/usr/bin/env python3
"""Read-only Alean SAPI smoke.

Uses environment-backed credentials only.  It never prints credentials,
request URLs/query strings, provider response bodies, offer names, or prices.
The synthetic search contains no customer PII and performs no booking/payment.
"""

from __future__ import annotations

import os
import sys
from dataclasses import replace
from datetime import date, timedelta

import requests

from shared.alean import AleanSettings, readiness_probe, search_tours


def main() -> int:
    settings = AleanSettings.from_env()
    if not settings.username or not settings.password:
        print("Alean SAPI smoke: BLOCKED credentials_missing")
        return 2

    # Smoke access is independent of the production-enable flag.
    settings = replace(settings, enabled=True)
    session = requests.Session()

    readiness = readiness_probe(settings, session)
    if not readiness.get("ok"):
        reason = readiness.get("reason", "unknown")
        status = readiness.get("status")
        suffix = f" status={status}" if status is not None else ""
        print(f"Alean SAPI dictionaries: FAIL reason={reason}{suffix}")
        return 1

    print(
        "Alean SAPI dictionaries: PASS "
        f"countries={readiness['countries']} "
        f"depart_cities={readiness['depart_cities']}"
    )

    start = date.today() + timedelta(days=30)
    end = start + timedelta(days=1)
    origin = os.getenv("ALEAN_SMOKE_ORIGIN", "Москва").strip()
    destination = os.getenv("ALEAN_SMOKE_DESTINATION", "Россия").strip()
    nights = max(1, min(28, int(os.getenv("ALEAN_SMOKE_NIGHTS", "7"))))

    info = {
        "destination": destination,
        "origin": origin,
        "dates": f"{start:%d.%m.%Y}-{end:%d.%m.%Y}",
        "nights": nights,
        "dates_are_trip": False,
        "people": "2",
        "kids_ages": [],
        "budget": 0,
        "budget_open_ended": True,
    }
    result = search_tours(settings, session, info)
    if result.offers:
        print(
            "Alean SAPI synthetic GetTours: PASS "
            f"normalized_offers={len(result.offers)}"
        )
        return 0

    # Empty inventory is a valid provider response, but authentication,
    # dictionary resolution and GetTours must still have completed.
    if result.error == "Подходящих туров пока не найдено":
        print("Alean SAPI synthetic GetTours: PASS normalized_offers=0")
        return 0

    print(
        "Alean SAPI synthetic GetTours: FAIL "
        f"reason={result.error or 'unknown'}"
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
