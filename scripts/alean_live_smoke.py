"""Read-only SAPI account check; no bot startup, customer messages or booking."""
from __future__ import annotations

import json
import time
from datetime import date, timedelta

import requests

from shared import alean


class AuditSession(requests.Session):
    def __init__(self):
        super().__init__()
        self.operations = []

    def get(self, url, **kwargs):
        operation = {"action": kwargs["params"]["action"]}
        self.operations.append(operation)
        response = super().get(url, **kwargs)
        operation["status"] = response.status_code
        return response


def main() -> int:
    settings = alean.AleanSettings.from_env()
    report = {"customerTrafficEnabledByCheck": False, "messagesSent": 0,
              "bookingCalls": 0, "authenticatedCountries": False,
              "syntheticSearch": "not_run"}
    if not settings.username or not settings.password:
        report["blocker"] = "Set ALEAN_USERNAME and ALEAN_PASSWORD securely"
        print(json.dumps(report, ensure_ascii=False))
        return 2

    # Enable only this isolated adapter instance, never the application or router.
    settings.enabled = True
    started = time.monotonic()
    with AuditSession() as session:
        try:
            countries = alean._catalog(settings, session, "GetCountries")
            if not countries:
                raise ValueError("Empty country catalog")
            report.update(authenticatedCountries=True, countryCount=len(countries))
            query = {"destination": "Сочи", "origin": "Москва",
                     "dates": (date.today() + timedelta(days=30)).isoformat(),
                     "dates_are_trip": False, "nights": 7, "people": 2,
                     "kids_ages": [7], "budget": 500000, "budget_scope": "total"}
            result = alean.search_tours(settings, session, query)
            searched = any(op["action"] == "GetTours" for op in session.operations)
            success = searched and not result.error
            report.update(syntheticSearch="passed" if success else "failed",
                          query=query, offerCount=len(result.offers))
            if result.error:
                report["blocker"] = result.error
        except Exception as exc:
            # Exception text can contain credentials or URLs; report only its type.
            report["blocker"] = type(exc).__name__
        report.update(operations=session.operations,
                      elapsedSeconds=round(time.monotonic() - started, 2))
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report["syntheticSearch"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
