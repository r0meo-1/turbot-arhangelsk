"""Tourvisor package-tour search used by the VK lead funnel.

The upstream search is asynchronous: start it, poll its status, then fetch
the accumulated results. Public helpers fail softly so an unavailable API can
never prevent the existing lead from reaching the manager.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Callable, Dict, List, Optional, Tuple

import requests

from shared.dates import parse_russian_dates


logger = logging.getLogger("turbot.shared.tourvisor")
RequestFn = Callable[[str, str, Optional[Dict[str, Any]]], Any]


@dataclass
class TourvisorSettings:
    enabled: bool = False
    token: str = ""
    base_url: str = "https://api.tourvisor.ru/search/api/v1"
    timeout: int = 15
    poll_interval: float = 3.0
    max_wait: float = 15.0
    max_offers: int = 3


@dataclass(frozen=True)
class SearchWindow:
    date_from: str
    date_to: str
    nights_from: int
    nights_to: int


@dataclass
class TourOffer:
    hotel: str
    category: int
    region: str
    date: str
    nights: int
    meal: str
    room: str
    operator: str
    price: int
    currency: str = "RUB"
    fuel_charge: int = 0


@dataclass
class SearchResult:
    offers: List[TourOffer] = field(default_factory=list)
    error: str = ""
    search_id: Optional[int] = None


def _next_saturday(today: date) -> date:
    return today + timedelta(days=((5 - today.weekday()) % 7 or 7))


def _next_month(today: date) -> date:
    if today.month == 12:
        return date(today.year + 1, 1, 1)
    return date(today.year, today.month + 1, 1)


def resolve_search_window(raw: str, today: Optional[date] = None) -> Optional[SearchWindow]:
    """Map the dialog's Russian dates to Tourvisor departure/nights ranges."""
    today = today or date.today()
    text = (raw or "").strip().lower()
    if not text:
        return None

    start_raw, end_raw = parse_russian_dates(raw)
    if start_raw:
        try:
            start = datetime.strptime(start_raw, "%Y-%m-%d").date()
            end = datetime.strptime(end_raw, "%Y-%m-%d").date() if end_raw else None
        except ValueError:
            start = None
            end = None
        if start and start >= today:
            nights = max(1, min((end - start).days if end and end > start else 7, 28))
            return SearchWindow(start.isoformat(), start.isoformat(), nights, nights)

    if "выходн" in text:
        start = _next_saturday(today)
        return SearchWindow(start.isoformat(), start.isoformat(), 2, 3)

    if "след" in text and "месяц" in text:
        start = _next_month(today)
        end = start + timedelta(days=20)
        return SearchWindow(start.isoformat(), end.isoformat(), 6, 12)

    if "эт" in text and "месяц" in text:
        start = today + timedelta(days=3)
        next_month = _next_month(today)
        end = min(next_month - timedelta(days=1), start + timedelta(days=20))
        if end < start:
            start = next_month
            end = start + timedelta(days=20)
        return SearchWindow(start.isoformat(), end.isoformat(), 6, 12)

    if "гибк" in text:
        start = today + timedelta(days=14)
        end = start + timedelta(days=21)
        return SearchWindow(start.isoformat(), end.isoformat(), 6, 12)

    return None


def _normalise_name(value: str) -> str:
    value = (value or "").strip().lower().replace("ё", "е")
    value = re.sub(r"[^a-zа-я0-9]+", " ", value)
    aliases = {
        "петербург": "санкт петербург",
        "спб": "санкт петербург",
        "эмираты": "оаэ",
        "объединенные арабские эмираты": "оаэ",
    }
    compact = " ".join(value.split())
    return aliases.get(compact, compact)


def _find_named_id(items: Any, wanted: str) -> Optional[int]:
    wanted_key = _normalise_name(wanted)
    if not isinstance(items, list) or not wanted_key:
        return None
    for item in items:
        if not isinstance(item, dict):
            continue
        names = [item.get("name"), item.get("russianName"), item.get("fullName")]
        if wanted_key in {_normalise_name(str(name or "")) for name in names}:
            try:
                return int(item["id"])
            except (KeyError, TypeError, ValueError):
                return None
    return None


def _people(info: Dict[str, Any]) -> Tuple[Optional[int], List[int], str]:
    raw = str(info.get("people") or "1")
    match = re.search(r"\d+", raw)
    adults = int(match.group()) if match else 1
    ages = [int(age) for age in (info.get("kids_ages") or [])]
    if adults > 6:
        return None, ages, "Tourvisor ищет максимум для 6 взрослых"
    if len(ages) > 3:
        return None, ages, "Tourvisor ищет максимум для 3 детей"
    return max(adults, 1), ages, ""


def _http_request(
    settings: TourvisorSettings,
    session: requests.Session,
    method: str,
    path: str,
    params: Optional[Dict[str, Any]],
) -> Any:
    response = session.request(
        method,
        settings.base_url.rstrip("/") + "/" + path.lstrip("/"),
        params=params or None,
        headers={"Authorization": f"Bearer {settings.token}", "Accept": "application/json"},
        timeout=settings.timeout,
    )
    response.raise_for_status()
    return response.json()


def _extract_offers(payload: Any, limit: int) -> List[TourOffer]:
    offers: List[TourOffer] = []
    if not isinstance(payload, list):
        return offers
    for hotel in payload:
        if not isinstance(hotel, dict):
            continue
        tours = [tour for tour in (hotel.get("tours") or []) if isinstance(tour, dict)]
        if not tours:
            continue
        tour = min(tours, key=lambda item: int(item.get("price") or 10**15))
        try:
            price = int(tour.get("price") or hotel.get("price") or 0)
        except (TypeError, ValueError):
            continue
        if price <= 0:
            continue
        meal = tour.get("meal") or {}
        operator = tour.get("operator") or {}
        region = hotel.get("subRegion") or hotel.get("region") or {}
        try:
            fuel_charge = int(tour.get("fuelCharge") or 0)
        except (TypeError, ValueError):
            fuel_charge = 0
        offers.append(TourOffer(
            hotel=str(hotel.get("name") or "Отель"),
            category=int(hotel.get("category") or 0),
            region=str(region.get("name") or ""),
            date=str(tour.get("date") or ""),
            nights=int(tour.get("nights") or 0),
            meal=str(meal.get("russianName") or meal.get("fullRussianName") or meal.get("name") or ""),
            room=str(tour.get("roomType") or tour.get("placement") or ""),
            operator=str(operator.get("russianName") or operator.get("fullName") or operator.get("name") or ""),
            price=price,
            currency=str(tour.get("currency") or hotel.get("currency") or "RUB"),
            fuel_charge=fuel_charge,
        ))
    offers.sort(key=lambda item: item.price + item.fuel_charge)
    # One cheapest room per hotel is enough for a compact messenger preview.
    unique: List[TourOffer] = []
    seen = set()
    for offer in offers:
        key = _normalise_name(offer.hotel)
        if key in seen:
            continue
        seen.add(key)
        unique.append(offer)
        if len(unique) >= max(1, limit):
            break
    return unique


def search_tours(
    settings: TourvisorSettings,
    session: requests.Session,
    info: Dict[str, Any],
    *,
    request_fn: Optional[RequestFn] = None,
    sleep_fn: Callable[[float], None] = time.sleep,
    log: Optional[logging.Logger] = None,
) -> SearchResult:
    """Search Tourvisor and return a compact, display-ready result."""
    log = log or logger
    if not settings.enabled or not settings.token:
        return SearchResult(error="Поиск туров сейчас выключен")
    if info.get("needs_consultation"):
        return SearchResult(error="Для направления нужна консультация менеджера")

    window = resolve_search_window(str(info.get("dates") or ""))
    if not window:
        return SearchResult(error="Не получилось определить даты для автоматического поиска")
    adults, child_ages, people_error = _people(info)
    if people_error or adults is None:
        return SearchResult(error=people_error)

    caller = request_fn or (
        lambda method, path, params=None: _http_request(settings, session, method, path, params)
    )
    try:
        departures = caller("GET", "departures", {"departureCountryId": 1})
        departure_id = _find_named_id(departures, str(info.get("origin") or ""))
        if departure_id is None:
            return SearchResult(error="Этот город вылета пока не найден в Tourvisor")

        countries = caller("GET", "countries", {"departureId": departure_id})
        country_id = _find_named_id(countries, str(info.get("destination") or ""))
        if country_id is None:
            return SearchResult(error="Это направление пока не найдено в Tourvisor")

        params: Dict[str, Any] = {
            "departureId": departure_id,
            "countryId": country_id,
            "dateFrom": window.date_from,
            "dateTo": window.date_to,
            "nightsFrom": window.nights_from,
            "nightsTo": window.nights_to,
            "adults": adults,
            "currency": "RUB",
            "onlyCharter": False,
        }
        if child_ages:
            params["childs"] = child_ages
        try:
            budget = int(info.get("budget") or 0)
        except (TypeError, ValueError):
            budget = 0
        if budget and not info.get("budget_open_ended"):
            params["priceTo"] = budget * (adults + len(child_ages))

        started = caller("GET", "tours/search", params)
        search_id = int((started or {}).get("searchId"))
        deadline = time.monotonic() + max(settings.max_wait, 0)
        while time.monotonic() < deadline:
            sleep_fn(max(settings.poll_interval, 0))
            status = caller(
                "GET", f"tours/search/{search_id}/status", {"operatorStatus": False}
            ) or {}
            state = str(status.get("status") or "").lower()
            if int(status.get("progress") or 0) >= 100 or state in {
                "complete", "completed", "finished", "done",
            }:
                break

        payload = caller("GET", f"tours/search/{search_id}", {"limit": 25})
        offers = _extract_offers(payload, settings.max_offers)
        if not offers:
            return SearchResult(error="Подходящих туров пока не найдено", search_id=search_id)
        return SearchResult(offers=offers, search_id=search_id)
    except Exception as exc:
        log.error("Tourvisor search failed: %s", exc)
        return SearchResult(error="Tourvisor временно не ответил")


def _format_price(value: int, currency: str) -> str:
    suffix = "₽" if currency.upper() in {"RUB", "RUR", ""} else currency.upper()
    return f"{value:,}".replace(",", " ") + f" {suffix}"


def format_client_message(result: SearchResult, max_len: int = 3500) -> str:
    if not result.offers:
        return ""
    lines = ["🔎 Нашёл несколько вариантов по вашей заявке:", ""]
    for index, offer in enumerate(result.offers, 1):
        stars = f" {offer.category}★" if offer.category else ""
        place = f" · {offer.region}" if offer.region else ""
        lines.append(f"{index}. {offer.hotel}{stars}{place}")
        details = []
        if offer.date:
            details.append(offer.date)
        if offer.nights:
            details.append(f"{offer.nights} ночей")
        if offer.meal:
            details.append(offer.meal)
        if details:
            lines.append("   📅 " + " · ".join(details))
        if offer.room:
            lines.append(f"   🛏 {offer.room}")
        price = _format_price(offer.price, offer.currency)
        if offer.fuel_charge:
            price += f" + сбор {_format_price(offer.fuel_charge, offer.currency)}"
        lines.append(f"   💰 от {price} за тур")
        if offer.operator:
            lines.append(f"   Туроператор: {offer.operator}")
        lines.append("")
    lines.append("Цены меняются онлайн. Менеджер проверит наличие и итоговую стоимость перед бронированием.")
    return "\n".join(lines)[:max_len]
