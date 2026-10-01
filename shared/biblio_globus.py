"""Bounded read-only Biblio-Globus export client; not a production router.

Protocol: https://export.bgoperator.ru/load-xml-prices.html
Cookies rotate on each request. Keep a dedicated session for this vendor.
Never call Tour API, booking forms, or partner price mutation endpoints.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
import json
import os
import re
import threading
import time
from typing import Any

import requests


class ExportError(RuntimeError):
    """Safe diagnostic; never includes a URL, response body or credentials."""


@dataclass(frozen=True)
class ExportSettings:
    enabled: bool = False
    login: str = field(default="", repr=False)
    password: str = field(default="", repr=False)
    timeout: float = 5.0
    max_response_bytes: int = 8 * 1024 * 1024

    @classmethod
    def from_env(cls) -> ExportSettings:
        return cls(
            enabled=os.getenv("BIBLIO_EXPORT_ENABLED", "false").lower() == "true",
            login=os.getenv("BIBLIO_EXPORT_LOGIN", ""),
            password=os.getenv("BIBLIO_EXPORT_PASSWORD", ""),
        )


def _id(value: str) -> str:
    value = str(value)
    if not re.fullmatch(r"[0-9]{1,20}", value):
        raise ExportError("invalid export identifier")
    return value


class ExportClient:
    """Explicit read methods only. Caller must close or use a context manager.

    Disabled settings make zero network calls. Injected sessions are borrowed
    for tests; in production the client owns its isolated cookie jar.
    """

    def __init__(self, settings: ExportSettings, *, session=None):
        if not 0 < settings.timeout <= 15 or not 0 < settings.max_response_bytes <= 32 * 1024 * 1024:
            raise ExportError("invalid export limits")
        self.settings = settings
        self._owns_session = session is None
        self._session = requests.Session() if session is None else session
        self._lock = threading.Lock()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def close(self):
        if self._owns_session:
            self._session.close()

    def _authenticate(self):
        response = self._session.post(
            "https://login.bgoperator.ru/auth",
            data={"login": self.settings.login, "pwd": self.settings.password},
            headers={"Accept-Encoding": "gzip"},
            timeout=self.settings.timeout, allow_redirects=False, stream=True,
        )
        try:
            if response.status_code not in (200, 302):
                raise ExportError("export authentication failed")
            names = {cookie.name for cookie in self._session.cookies}
            if not ("L" in names and any(re.fullmatch(r"A\d+", n) for n in names)
                    and any(re.fullmatch(r"Z\d+", n) for n in names)):
                raise ExportError("export authentication cookies missing")
        finally:
            response.close()

    def _read(self, path: str, params: dict[str, Any] | None = None):
        if not self.settings.enabled or not self.settings.login or not self.settings.password:
            raise ExportError("export is disabled or unconfigured")
        # Serialize the rotating Z cookie and allow at most one login retry.
        with self._lock:
            try:
                for attempt in range(2):
                    started = time.monotonic()
                    response = self._session.get(
                        "https://export.bgoperator.ru" + path,
                        params=params, headers={"Accept-Encoding": "gzip"},
                        timeout=self.settings.timeout, allow_redirects=False, stream=True,
                    )
                    try:
                        if response.status_code == 401 and attempt == 0:
                            response.close()
                            self._authenticate()
                            continue
                        if response.status_code != 200:
                            raise ExportError(f"export HTTP {response.status_code}")
                        data = bytearray()
                        for chunk in response.iter_content(chunk_size=65536):
                            if time.monotonic() - started > 15:
                                raise ExportError("export timeout")
                            data.extend(chunk)
                            if len(data) > self.settings.max_response_bytes:
                                raise ExportError("export response exceeds size limit")
                        payload = json.loads(data)
                        if not isinstance(payload, (dict, list)):
                            raise ExportError("invalid export response shape")
                        return payload
                    finally:
                        response.close()
            except requests.Timeout:
                raise ExportError("export timeout") from None
            except requests.RequestException:
                raise ExportError("export transport failure") from None
            except (ValueError, UnicodeError):
                raise ExportError("invalid export JSON") from None
        raise ExportError("export authentication failed")

    def countries(self):
        return self._read("/yandex", {"action": "countries"})

    def resorts(self):
        return self._read("/auto/jsonResorts.json")

    def hotels(self, hotel_id: str):
        # Avoid an unbounded full-hotel download; inspect one known hotel.
        return self._read("/yandex", {"action": "hotelsJson", "id": _id(hotel_id)})

    def price_lists(self, country_id: str, departure_id: str):
        return self._read("/yandex", {
            "action": "files", "flt": _id(country_id),
            "flt2": _id(departure_id), "xml": 11,
        })

    def prices(self, country_id: str, departure_id: str, price_id: str,
               departure_date: date, nights: int):
        if type(nights) is not int or not 1 <= nights <= 28 or type(departure_date) is not date:
            raise ExportError("invalid export dates or nights")
        # Reconstruct an allowlisted read endpoint. Never follow upstream url,
        # href0/1/2 (booking links), or send tourist identities/passports.
        return self._read("/partners", {
            "action": "price", "tid": 211, "flt": _id(country_id),
            "flt2": _id(departure_id), "id_price": _id(price_id),
            "data": departure_date.strftime("%d.%m.%Y"), "xml": 11,
            "f7": nights, "novirt": 0,
        })
