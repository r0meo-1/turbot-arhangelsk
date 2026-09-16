"""Travelata package-tour provider for TurBot.

Implements the partner API introduced in June 2026. Credentials are never
logged. Public helpers fail softly so a provider outage cannot block the lead
funnel.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import requests

from shared import tourvisor as _tourvisor

logger = logging.getLogger("turbot.shared.travelata")


@dataclass
class TravelataSettings:
    enabled: bool = False
    username: str = ""
    password: str = ""
    base_url: str = "https://api-gateway.travelata.ru"
    timeout: int = 15
    max_offers: int = 15


def _request(
    settings: TravelataSettings,
    session: requests.Session,
    path: str,
    params: Optional[Dict[str, Any]] = None,
) -> Any:
    response = session.get(
        settings.base_url.rstrip("/") + "/partners/" + path.lstrip("/"),
        params=params or None,
        auth=(settings.username, settings.password),
        headers={"Accept": "application/json"},
        timeout=settings.timeout,
    )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict) or payload.get("success") is not True:
        raise RuntimeError("Travelata API returned an unsuccessful response")
    return payload.get("result") or []


def _category(value: Any) -> int:
    match = re.search(r"\d+", str(value or ""))
    return int(match.group()) if match else 0


def _map_by_id(items: Any, *, label_key: str = "name") -> Dict[int, str]:
    result: Dict[int, str] = {}
    if not isinstance(items, list):
        return result
    for item in items:
        if not isinstance(item, dict):
            continue
        try:
            item_id = int(item.get("id"))
        except (TypeError, ValueError):
            continue
        result[item_id] = str(item.get(label_key) or item.get("name") or "").strip()
    return result


def search_tours(
    settings: TravelataSettings,
    session: requests.Session,
    info: Dict[str, Any],
    *,
    log: Optional[logging.Logger] = None,
) -> _tourvisor.SearchResult:
    """Search Travelata and normalise offers to the existing TurBot model."""
    log = log or logger
    if not settings.enabled or not settings.username or not settings.password:
        return _tourvisor.SearchResult(error="Travelata сейчас не настроена")
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
        departures = _request(
            settings, session, "directory/departureCities", {"disabled": 0}
        )
        departure_id = _tourvisor._find_named_id(
            departures, str(info.get("origin") or "")
        )
        if departure_id is None:
            return _tourvisor.SearchResult(
                error="Этот город вылета пока не найден в Travelata"
            )

        countries = _request(settings, session, "directory/countries", {"disabled": 0})
        country_id = _tourvisor._find_named_id(
            countries, str(info.get("destination") or "")
        )
        if country_id is None:
            return _tourvisor.SearchResult(
                error="Это направление пока не найдено в Travelata"
            )

        kids_ages = [age for age in child_ages if 2 <= age <= 11]
        infants = [age for age in child_ages if age < 2]
        params: Dict[str, Any] = {
            "countries[]": country_id,
            "departureCity": departure_id,
            "touristGroup[adults]": adults,
            "touristGroup[kids]": len(kids_ages),
            "touristGroup[infants]": len(infants),
            "checkInDateRange[from]": window.date_from,
            "checkInDateRange[to]": window.date_to,
            "nightRange[from]": window.nights_from,
            "nightRange[to]": window.nights_to,
        }
        if child_ages:
            params["touristGroup[kidsAges][]"] = child_ages

        raw_offers = _request(settings, session, "statistic/cheapestTours", params)
        if not isinstance(raw_offers, list):
            raw_offers = []

        resort_ids = sorted({
            int(item.get("resortId")) for item in raw_offers
            if isinstance(item, dict) and str(item.get("resortId") or "").isdigit()
        })
        meal_ids = sorted({
            int(item.get("mealId")) for item in raw_offers
            if isinstance(item, dict) and str(item.get("mealId") or "").isdigit()
        })
        resort_names: Dict[int, str] = {}
        meal_names: Dict[int, str] = {}
        if resort_ids:
            resort_names = _map_by_id(_request(
                settings, session, "directory/resorts",
                {"id[]": resort_ids, "limit": min(1000, max(100, len(resort_ids)))},
            ))
        if meal_ids:
            meal_names = _map_by_id(_request(
                settings, session, "directory/meals", {"id[]": meal_ids}
            ))

        offers: List[_tourvisor.TourOffer] = []
        had_positive_price = False
        for item in raw_offers:
            if not isinstance(item, dict):
                continue
            try:
                price = int(item.get("price") or 0)
            except (TypeError, ValueError):
                continue
            if price <= 0:
                continue
            had_positive_price = True

            try:
                resort_id = int(item.get("resortId") or 0)
            except (TypeError, ValueError):
                resort_id = 0
            try:
                meal_id = int(item.get("mealId") or 0)
            except (TypeError, ValueError):
                meal_id = 0
            try:
                rating = float(item.get("hotelRating") or 0)
            except (TypeError, ValueError):
                rating = 0.0

            hotel = str(item.get("hotelName") or "Отель").strip() or "Отель"
            if info.get("hotel_query") and not _tourvisor._hotel_matches(
                hotel, str(info.get("hotel_query") or "")
            ):
                continue

            offers.append(_tourvisor.TourOffer(
                hotel=hotel,
                category=_category(
                    item.get("hotelCategoryName") or item.get("hotelCategory")
                ),
                region=resort_names.get(resort_id, ""),
                date=str(item.get("checkinDate") or ""),
                nights=int(item.get("nights") or 0),
                meal=meal_names.get(meal_id, ""),
                room="",
                operator="",
                price=price,
                currency="RUB",
                fuel_charge=0,  # Travelata documents price as fuel-inclusive.
                tour_id="travelata:" + str(
                    item.get("tourIdentity") or item.get("hotelId") or ""
                ),
                picture_url=str(item.get("hotelPreview") or ""),
                departure=str(info.get("origin") or ""),
                rating=rating,
            ))

        offers.sort(key=lambda offer: offer.price)

        try:
            budget = int(info.get("budget") or 0)
        except (TypeError, ValueError):
            budget = 0
        if budget and not info.get("budget_open_ended"):
            cap = budget
            if info.get("budget_scope") != "total":
                cap *= adults + len(child_ages)
            offers = [offer for offer in offers if offer.price <= cap]

        offers = offers[: max(1, settings.max_offers)]
        if not offers:
            # A negative sentinel means a real upstream search completed. The VK
            # layer can use it to offer a deliberate over-budget retry.
            return _tourvisor.SearchResult(
                error="Подходящих туров пока не найдено",
                search_id=-1 if had_positive_price else None,
            )
        return _tourvisor.SearchResult(offers=offers, search_id=-1)
    except requests.HTTPError as exc:
        status = exc.response.status_code if exc.response is not None else None
        if status == 429:
            log.warning("Travelata rate limit reached")
            return _tourvisor.SearchResult(error="Travelata временно ограничила частоту запросов")
        if status in (401, 403):
            log.error("Travelata authorization/access failed: HTTP %s", status)
            return _tourvisor.SearchResult(error="Travelata сейчас недоступна по настройкам доступа")
        log.error("Travelata HTTP failure: %s", status)
        return _tourvisor.SearchResult(error="Travelata временно не ответила")
    except Exception as exc:
        log.error("Travelata search failed: %s", type(exc).__name__)
        return _tourvisor.SearchResult(error="Travelata временно не ответила")
