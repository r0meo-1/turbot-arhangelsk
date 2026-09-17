"""Sletat.ru package-tour provider for TurBot.

The official JSON gateway is asynchronous: create a search with ``GetTours``,
poll ``GetLoadState`` from the same server/IP, then fetch fresh results with
``GetTours`` + ``requestId``/``updateResult=1``. Credentials are accepted only
from environment-backed settings and are never logged.
"""

from __future__ import annotations

import logging
import os
import re
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

import requests

from shared import tourvisor as _tourvisor

logger = logging.getLogger("turbot.shared.sletat")


@dataclass
class SletatSettings:
    enabled: bool = False
    login: str = ""
    password: str = ""
    base_url: str = "https://module.sletat.ru/Main.svc"
    timeout: int = 20
    poll_interval: float = 1.5
    max_wait: float = 30.0
    max_offers: int = 15

    @classmethod
    def from_env(cls) -> "SletatSettings":
        login = os.getenv("SLETAT_LOGIN", "").strip()
        password = os.getenv("SLETAT_PASSWORD", "").strip()
        enabled_raw = os.getenv(
            "VK_SLETAT_ENABLED", "true" if login and password else "false"
        ).strip().lower()

        def _int(name: str, default: int) -> int:
            try:
                return int(os.getenv(name, str(default)).strip() or default)
            except ValueError:
                return default

        def _float(name: str, default: float) -> float:
            try:
                return float(os.getenv(name, str(default)).strip() or default)
            except ValueError:
                return default

        return cls(
            enabled=enabled_raw in {"1", "true", "yes", "on"},
            login=login,
            password=password,
            base_url=os.getenv(
                "SLETAT_BASE_URL", "https://module.sletat.ru/Main.svc"
            ).strip(),
            timeout=max(5, _int("SLETAT_TIMEOUT", 20)),
            poll_interval=max(1.5, _float("SLETAT_POLL_INTERVAL", 1.5)),
            max_wait=max(3.0, _float("SLETAT_MAX_WAIT", 30.0)),
            max_offers=max(1, _int("SLETAT_MAX_OFFERS", 15)),
        )


def _request(
    settings: SletatSettings,
    session: requests.Session,
    method: str,
    params: Optional[Dict[str, Any]] = None,
) -> Any:
    query = dict(params or {})
    query.setdefault("login", settings.login)
    query.setdefault("password", settings.password)
    response = session.get(
        settings.base_url.rstrip("/") + "/" + method,
        params=query,
        headers={"Accept": "application/json"},
        timeout=settings.timeout,
    )
    response.raise_for_status()
    payload = response.json()
    wrapper = payload.get(f"{method}Result") if isinstance(payload, dict) else None
    if not isinstance(wrapper, dict):
        raise RuntimeError("Sletat.ru returned an unexpected response")
    if wrapper.get("IsError"):
        raise RuntimeError(str(wrapper.get("ErrorMessage") or "Sletat.ru API error"))
    return wrapper.get("Data")


def _find_id(items: Any, wanted: str) -> Optional[int]:
    key = _tourvisor._normalise_name(wanted)
    if not key or not isinstance(items, list):
        return None
    for item in items:
        if not isinstance(item, dict):
            continue
        names = (item.get("Name"), item.get("OriginalName"), item.get("name"))
        if key not in {_tourvisor._normalise_name(str(value or "")) for value in names}:
            continue
        try:
            return int(item.get("Id") if item.get("Id") is not None else item.get("id"))
        except (TypeError, ValueError):
            return None
    return None


def _iso_to_sletat(value: str) -> str:
    return datetime.strptime(value, "%Y-%m-%d").strftime("%d/%m/%Y")


def _category(value: Any) -> int:
    match = re.search(r"\d+", str(value or ""))
    return int(match.group()) if match else 0


def _row(row: Any, index: int, default: Any = "") -> Any:
    if not isinstance(row, list) or index >= len(row):
        return default
    value = row[index]
    return default if value is None else value


def _extract_offers(rows: Any, info: Dict[str, Any], limit: int) -> List[_tourvisor.TourOffer]:
    if not isinstance(rows, list):
        return []
    offers: List[_tourvisor.TourOffer] = []
    for row in rows:
        if not isinstance(row, list):
            continue
        try:
            price = int(float(_row(row, 42, 0) or 0))
        except (TypeError, ValueError):
            price = 0
        if price <= 0:
            # Older licences/responses can expose only the formatted field.
            match = re.search(r"\d+", str(_row(row, 15, "")).replace(" ", ""))
            price = int(match.group()) if match else 0
        if price <= 0:
            continue

        hotel = str(_row(row, 7, "Отель") or "Отель").strip()
        if info.get("hotel_query") and not _tourvisor._hotel_matches(
            hotel, str(info.get("hotel_query") or "")
        ):
            continue
        try:
            nights = int(_row(row, 14, 0) or 0)
        except (TypeError, ValueError):
            nights = 0
        try:
            rating = float(_row(row, 35, 0) or 0)
        except (TypeError, ValueError):
            rating = 0.0

        offer_id = str(_row(row, 0, "") or "")
        source_id = str(_row(row, 1, "") or "")
        offers.append(_tourvisor.TourOffer(
            hotel=hotel,
            category=_category(_row(row, 8, "")),
            region=str(_row(row, 19, "") or ""),
            date=str(_row(row, 12, "") or ""),
            nights=nights,
            meal=str(_row(row, 10, "") or ""),
            room=str(_row(row, 9, "") or _row(row, 11, "") or ""),
            operator=str(_row(row, 18, "") or ""),
            price=price,
            currency=str(_row(row, 43, "RUB") or "RUB"),
            fuel_charge=0,
            tour_id=f"sletat:{source_id}:{offer_id}",
            picture_url=str(_row(row, 29, "") or ""),
            departure=str(_row(row, 33, "") or info.get("origin") or ""),
            rating=rating,
            beach_line=str(_row(row, 87, "") or ""),
        ))

    offers.sort(key=lambda item: item.price)
    unique: List[_tourvisor.TourOffer] = []
    seen = set()
    for offer in offers:
        key = _tourvisor._normalise_name(offer.hotel)
        if key in seen:
            continue
        seen.add(key)
        unique.append(offer)
        if len(unique) >= max(1, limit):
            break
    return unique


def search_tours(
    settings: SletatSettings,
    session: requests.Session,
    info: Dict[str, Any],
    *,
    sleep_fn: Callable[[float], None] = time.sleep,
    log: Optional[logging.Logger] = None,
) -> _tourvisor.SearchResult:
    """Search Sletat.ru and normalise results into the TurBot offer model."""
    log = log or logger
    if not settings.enabled or not settings.login or not settings.password:
        return _tourvisor.SearchResult(error="Слетать.ру сейчас не настроен")
    if info.get("needs_consultation"):
        return _tourvisor.SearchResult(error="Для направления нужна консультация менеджера")

    nights_raw = info.get("nights") if info.get("dates_are_trip") is False else None
    window = _tourvisor.resolve_search_window(
        str(info.get("dates") or ""), nights_raw=nights_raw
    )
    if not window:
        return _tourvisor.SearchResult(
            error="Не получилось определить даты для автоматического поиска"
        )
    adults, child_ages, people_error = _tourvisor._people(info)
    if people_error or adults is None:
        return _tourvisor.SearchResult(error=people_error)

    try:
        departures = _request(settings, session, "GetDepartCities")
        departure_id = _find_id(departures, str(info.get("origin") or ""))
        if departure_id is None:
            return _tourvisor.SearchResult(
                error="Этот город вылета пока не найден в Слетать.ру"
            )

        countries = _request(
            settings, session, "GetCountries", {"townFromId": departure_id}
        )
        country_id = _find_id(countries, str(info.get("destination") or ""))
        if country_id is None:
            return _tourvisor.SearchResult(
                error="Это направление пока не найдено в Слетать.ру"
            )

        params: Dict[str, Any] = {
            "cityFromId": departure_id,
            "countryId": country_id,
            "currencyAlias": "RUB",
            "s_adults": adults,
            "s_kids": len(child_ages),
            "s_nightsMin": window.nights_from,
            "s_nightsMax": window.nights_to,
            "s_departFrom": _iso_to_sletat(window.date_from),
            "s_departTo": _iso_to_sletat(window.date_to),
            "s_hasTickets": "true",
            "s_ticketsIncluded": "true",
            "s_hotelIsNotInStop": "true",
            "calcFullPrice": 1,
            "includeOilTaxesAndVisa": 1,
            "includeDescriptions": 0,
            "showHotelFacilities": 0,
            "groupBy": "so_price",
            "pageSize": max(25, min(100, settings.max_offers * 4)),
            "pageNumber": 1,
            "requestId": 0,
            "updateResult": 0,
        }
        if child_ages:
            params["s_kids_ages"] = ",".join(str(age) for age in child_ages)
        if info.get("direct_only"):
            params["filterToursForType"] = 524288

        try:
            budget = int(info.get("budget") or 0)
        except (TypeError, ValueError):
            budget = 0
        total_cap = 0
        if budget and not info.get("budget_open_ended"):
            total_cap = budget
            if info.get("budget_scope") != "total":
                total_cap *= adults + len(child_ages)
            params["s_priceMax"] = total_cap

        started = _request(settings, session, "GetTours", params)
        if not isinstance(started, dict):
            raise RuntimeError("Sletat.ru did not return a search id")
        search_id = int(started.get("requestId") or 0)
        if search_id <= 0:
            raise RuntimeError("Sletat.ru did not return a search id")

        deadline = time.monotonic() + settings.max_wait
        while time.monotonic() < deadline:
            sleep_fn(settings.poll_interval)
            state = _request(
                settings, session, "GetLoadState", {"requestId": search_id}
            )
            if not isinstance(state, list) or not state:
                continue
            # Return early once inventory exists; this keeps messenger latency
            # bounded while respecting Sletat's required GetLoadState polling.
            if any(int(item.get("RowsCount") or 0) > 0 for item in state if isinstance(item, dict)):
                break
            if all(bool(item.get("IsProcessed")) for item in state if isinstance(item, dict)):
                break

        final_params = dict(params)
        final_params.update(requestId=search_id, updateResult=1)
        finished = _request(settings, session, "GetTours", final_params)
        data = finished if isinstance(finished, dict) else {}
        offers = _extract_offers(data.get("aaData") or [], info, settings.max_offers)
        if total_cap:
            offers = [offer for offer in offers if offer.price <= total_cap]
        if not offers:
            return _tourvisor.SearchResult(
                error="Подходящих туров пока не найдено", search_id=search_id
            )
        return _tourvisor.SearchResult(offers=offers, search_id=search_id)
    except requests.HTTPError as exc:
        status = exc.response.status_code if exc.response is not None else None
        if status in (401, 403):
            log.error("Sletat.ru authorization/access failed: HTTP %s", status)
            return _tourvisor.SearchResult(
                error="Слетать.ру сейчас недоступен по настройкам доступа"
            )
        if status == 429:
            return _tourvisor.SearchResult(
                error="Слетать.ру временно ограничил частоту запросов"
            )
        log.error("Sletat.ru HTTP failure: %s", status)
        return _tourvisor.SearchResult(error="Слетать.ру временно не ответил")
    except Exception as exc:
        log.error("Sletat.ru search failed: %s", type(exc).__name__)
        return _tourvisor.SearchResult(error="Слетать.ру временно не ответил")
