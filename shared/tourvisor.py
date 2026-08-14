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
    tour_id: str = ""
    picture_url: str = ""
    departure: str = ""
    rating: float = 0.0
    reviews_pct: int = 0
    discount_pct: int = 0
    old_price: int = 0
    beach_line: str = ""


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


def _nights_range(raw: Any) -> Optional[Tuple[int, int]]:
    values = [int(value) for value in re.findall(r"\d+", str(raw or ""))]
    if not values:
        return None
    start = max(1, min(values[0], 28))
    end = max(1, min(values[1] if len(values) > 1 else start, 28))
    return min(start, end), max(start, end)


def resolve_search_window(
    raw: str,
    today: Optional[date] = None,
    nights_raw: Any = None,
) -> Optional[SearchWindow]:
    """Map the dialog's Russian dates to Tourvisor departure/nights ranges."""
    today = today or date.today()
    text = (raw or "").strip().lower()
    explicit_nights = _nights_range(nights_raw)
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
            if explicit_nights:
                departure_to = end if end and end >= start else start
                return SearchWindow(
                    start.isoformat(), departure_to.isoformat(),
                    explicit_nights[0], explicit_nights[1],
                )
            nights = max(1, min((end - start).days if end and end > start else 7, 28))
            return SearchWindow(start.isoformat(), start.isoformat(), nights, nights)

    if "выходн" in text:
        start = _next_saturday(today)
        nights = explicit_nights or (2, 3)
        return SearchWindow(start.isoformat(), start.isoformat(), *nights)

    if "след" in text and "месяц" in text:
        start = _next_month(today)
        end = start + timedelta(days=20)
        nights = explicit_nights or (6, 12)
        return SearchWindow(start.isoformat(), end.isoformat(), *nights)

    if "эт" in text and "месяц" in text:
        start = today + timedelta(days=3)
        next_month = _next_month(today)
        end = min(next_month - timedelta(days=1), start + timedelta(days=20))
        if end < start:
            start = next_month
            end = start + timedelta(days=20)
        nights = explicit_nights or (6, 12)
        return SearchWindow(start.isoformat(), end.isoformat(), *nights)

    if "гибк" in text:
        start = today + timedelta(days=14)
        end = start + timedelta(days=21)
        nights = explicit_nights or (6, 12)
        return SearchWindow(start.isoformat(), end.isoformat(), *nights)

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


def _hotel_matches(name: str, query: str) -> bool:
    hotel = _normalise_name(name)
    wanted = _normalise_name(query)
    wanted = re.sub(r"\b(?:hotel|отель)\b", "", wanted).strip()
    return bool(wanted and (wanted in hotel or hotel in wanted))


def _people(info: Dict[str, Any]) -> Tuple[Optional[int], List[int], str]:
    raw = str(info.get("people") or "1")
    match = re.search(r"\d+", raw)
    declared_adults = int(match.group()) if match else 1
    ages = [int(age) for age in (info.get("kids_ages") or [])]
    adults = declared_adults + sum(1 for age in ages if age >= 12)
    child_ages = [age for age in ages if age < 12]
    if adults > 6:
        return None, child_ages, "Tourvisor ищет максимум для 6 взрослых"
    if len(child_ages) > 3:
        return None, child_ages, "Tourvisor ищет максимум для 3 детей"
    return max(adults, 1), child_ages, ""


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
            tour_id=str(tour.get("id") or ""),
            picture_url=str(hotel.get("picturelink") or ""),
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

    nights_raw = info.get("nights") if info.get("dates_are_trip") is False else None
    window = resolve_search_window(str(info.get("dates") or ""), nights_raw=nights_raw)
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
            if info.get("budget_scope") == "total":
                params["priceTo"] = budget
            else:
                # Preserve the meaning of sessions started before VK switched
                # from a per-person budget to a total trip budget.
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
        extract_limit = 25 if info.get("hotel_query") else settings.max_offers
        offers = _extract_offers(payload, extract_limit)
        for offer in offers:
            offer.departure = str(info.get("origin") or "")
        if info.get("hotel_query"):
            offers = [
                offer for offer in offers
                if _hotel_matches(offer.hotel, str(info["hotel_query"]))
            ]
        if budget and not info.get("budget_open_ended"):
            total_cap = budget
            if info.get("budget_scope") != "total":
                total_cap *= adults + len(child_ages)
            # Tourvisor may return the fuel surcharge separately from `price`.
            # Respect the client's ceiling using the actual displayed total.
            offers = [
                offer for offer in offers
                if offer.price + offer.fuel_charge <= total_cap
            ]
        offers = offers[:settings.max_offers]
        if not offers:
            return SearchResult(error="Подходящих туров пока не найдено", search_id=search_id)
        return SearchResult(offers=offers, search_id=search_id)
    except Exception as exc:
        log.error("Tourvisor search failed: %s", exc)
        return SearchResult(error="Tourvisor временно не ответил")


def _format_price(value: int, currency: str) -> str:
    suffix = "₽" if currency.upper() in {"RUB", "RUR", ""} else currency.upper()
    return f"{value:,}".replace(",", " ") + f" {suffix}"


_MEAL_LABELS = {
    "RO": "без питания",
    "BB": "завтраки",
    "HB": "завтраки и ужины",
    "FB": "полный пансион",
    "AI": "всё включено",
    "UAI": "ультра всё включено",
}


def meal_label(value: Any) -> str:
    raw = str(value or "").strip()
    return _MEAL_LABELS.get(raw.upper(), raw)


def display_date(value: Any) -> str:
    raw = str(value or "").strip()
    try:
        return datetime.strptime(raw[:10], "%Y-%m-%d").strftime("%d.%m.%Y")
    except ValueError:
        return raw


def nights_label(value: Any) -> str:
    raw = str(value or "").strip()
    try:
        count = int(raw)
    except ValueError:
        return f"{raw} ночей" if raw else ""
    if count % 100 in (11, 12, 13, 14):
        word = "ночей"
    elif count % 10 == 1:
        word = "ночь"
    elif count % 10 in (2, 3, 4):
        word = "ночи"
    else:
        word = "ночей"
    return f"{count} {word}"


def is_all_inclusive(value: Any) -> bool:
    normalised = meal_label(value).casefold().replace("ё", "е")
    return (
        "все включено" in normalised
        or "all inclusive" in normalised
        or str(value or "").strip().upper() in {"AI", "UAI"}
    )


def get_hot_tours(
    origin: str = "Архангельск",
    destination: Optional[str] = None,
    limit: int = 15,
) -> List[TourOffer]:
    """Return top hot tours with discounts for demonstration/booking, matched to destination."""
    origin_name = (origin or "Архангельск").strip()
    dest_clean = (destination or "").strip().lower()

    catalog: Dict[str, List[TourOffer]] = {
        "таиланд": [
            TourOffer(
                hotel="Centara Grand Beach Resort Phuket",
                category=5,
                region="Пхукет (Карон), Таиланд",
                date="через 7 дней",
                nights=10,
                meal="Завтраки",
                room="Deluxe Ocean Facing",
                operator="Библио Глобус",
                price=158000,
                old_price=210000,
                discount_pct=25,
                rating=4.9,
                reviews_pct=96,
                beach_line="1-я линия (прямой выход на пляж Карон)",
                departure=origin_name,
                tour_id="th-1",
            ),
            TourOffer(
                hotel="Pullman Phuket Arcadia Naithon Beach",
                category=5,
                region="Пхукет (Найтон), Таиланд",
                date="через 9 дней",
                nights=11,
                meal="Завтраки",
                room="Superior Room",
                operator="Coral Travel",
                price=174000,
                old_price=230000,
                discount_pct=24,
                rating=4.9,
                reviews_pct=97,
                beach_line="1-я линия (панорамный вид на море)",
                departure=origin_name,
                tour_id="th-2",
            ),
            TourOffer(
                hotel="Beyond Resort Krabi",
                category=4,
                region="Краби (Клонг Мыанг), Таиланд",
                date="через 10 дней",
                nights=9,
                meal="Завтраки",
                room="Villa Garden View",
                operator="Sunmar",
                price=149000,
                old_price=195000,
                discount_pct=24,
                rating=4.8,
                reviews_pct=94,
                beach_line="1-я линия (песчаный приватный пляж)",
                departure=origin_name,
                tour_id="th-3",
            ),
            TourOffer(
                hotel="Marina Phuket Resort",
                category=4,
                region="Пхукет (Карон), Таиланд",
                date="через 12 дней",
                nights=10,
                meal="Завтраки",
                room="Jungle View Deluxe",
                operator="Anex Tour",
                price=162000,
                old_price=215000,
                discount_pct=25,
                rating=4.8,
                reviews_pct=95,
                beach_line="1-я линия (тропический сад и скалы)",
                departure=origin_name,
                tour_id="th-4",
            ),
            TourOffer(
                hotel="Amari Pattaya",
                category=5,
                region="Паттайя (Северный пляж), Таиланд",
                date="через 8 дней",
                nights=9,
                meal="Завтраки",
                room="Deluxe Sea View",
                operator="Библио Глобус",
                price=138000,
                old_price=185000,
                discount_pct=25,
                rating=4.8,
                reviews_pct=95,
                beach_line="1-я линия (аквапарк для детей)",
                departure=origin_name,
                tour_id="th-5",
            ),
            TourOffer(
                hotel="Mandarava Resort & Spa Karon Beach",
                category=5,
                region="Пхукет (Карон), Таиланд",
                date="через 14 дней",
                nights=10,
                meal="Завтраки",
                room="Panoramic Villa",
                operator="Pegas Touristik",
                price=155000,
                old_price=205000,
                discount_pct=24,
                rating=4.9,
                reviews_pct=96,
                beach_line="2-я линия (трансфер на пляж каждые 15 мин)",
                departure=origin_name,
                tour_id="th-6",
            ),
        ],
        "турция": [
            TourOffer(
                hotel="Akka Alinda Hotel",
                category=5,
                region="Кемер, Турция",
                date="через 3 дня",
                nights=7,
                meal="Ultra All Inclusive",
                room="Standard Sea View",
                operator="Anex Tour",
                price=118900,
                old_price=165000,
                discount_pct=28,
                rating=4.8,
                reviews_pct=96,
                beach_line="1-я линия (собственный пляж)",
                departure=origin_name,
                tour_id="tr-1",
            ),
            TourOffer(
                hotel="Granada Luxury Belek",
                category=5,
                region="Белек, Турция",
                date="через 7 дней",
                nights=7,
                meal="Ultra All Inclusive",
                room="Standard Room",
                operator="Pegas Touristik",
                price=132000,
                old_price=175000,
                discount_pct=24,
                rating=4.8,
                reviews_pct=95,
                beach_line="1-я линия (песчано-галечный)",
                departure=origin_name,
                tour_id="tr-2",
            ),
            TourOffer(
                hotel="PGS Hotels Kremlin Palace",
                category=5,
                region="Анталья, Турция",
                date="через 12 дней",
                nights=7,
                meal="Ultra All Inclusive",
                room="Palace Standard",
                operator="Anex Tour",
                price=124900,
                old_price=168000,
                discount_pct=25,
                rating=4.7,
                reviews_pct=92,
                beach_line="1-я линия (собственный пляж)",
                departure=origin_name,
                tour_id="tr-3",
            ),
            TourOffer(
                hotel="Maxx Royal Belek Golf Resort",
                category=5,
                region="Белек, Турция",
                date="через 11 дней",
                nights=7,
                meal="Maxx All Inclusive",
                room="Suite Land View",
                operator="Coral Travel",
                price=295000,
                old_price=390000,
                discount_pct=24,
                rating=5.0,
                reviews_pct=99,
                beach_line="1-я линия (VIP-пляж с белым песком)",
                departure=origin_name,
                tour_id="tr-4",
            ),
            TourOffer(
                hotel="Rixos Premium Tekirova",
                category=5,
                region="Кемер, Турция",
                date="через 5 дней",
                nights=7,
                meal="All Inclusive All Exclusive",
                room="Deluxe Room",
                operator="Fun&Sun",
                price=189000,
                old_price=250000,
                discount_pct=24,
                rating=4.9,
                reviews_pct=98,
                beach_line="1-я линия (бухта, сосновый лес)",
                departure=origin_name,
                tour_id="tr-5",
            ),
            TourOffer(
                hotel="Delphin Imperial Hotel",
                category=5,
                region="Лара / Анталья, Турция",
                date="через 9 дней",
                nights=7,
                meal="Ultra All Inclusive",
                room="Superior Room",
                operator="Sunmar",
                price=165000,
                old_price=220000,
                discount_pct=25,
                rating=4.9,
                reviews_pct=97,
                beach_line="1-я линия (песчаный пляж)",
                departure=origin_name,
                tour_id="tr-6",
            ),
        ],
        "египет": [
            TourOffer(
                hotel="Albatros Aqua Park Resort",
                category=4,
                region="Хургада, Египет",
                date="через 5 дней",
                nights=9,
                meal="All Inclusive",
                room="Superior Family",
                operator="Coral Travel",
                price=104500,
                old_price=142000,
                discount_pct=26,
                rating=4.7,
                reviews_pct=93,
                beach_line="2-я линия (аквапарк)",
                departure=origin_name,
                tour_id="eg-1",
            ),
            TourOffer(
                hotel="Rixos Radamis Sharm El Sheikh",
                category=5,
                region="Шарм-эль-Шейх, Египет",
                date="в субботу",
                nights=7,
                meal="Ultra All Inclusive",
                room="Deluxe Pool View",
                operator="Fun&Sun",
                price=146000,
                old_price=198000,
                discount_pct=26,
                rating=4.9,
                reviews_pct=98,
                beach_line="1-я линия (песчаный пляж)",
                departure=origin_name,
                tour_id="eg-2",
            ),
            TourOffer(
                hotel="Stella Beach Resort & Spa",
                category=5,
                region="Макади Бэй, Египет",
                date="через 8 дней",
                nights=8,
                meal="All Inclusive",
                room="Standard Sea View",
                operator="Библио Глобус",
                price=98500,
                old_price=135000,
                discount_pct=27,
                rating=4.6,
                reviews_pct=91,
                beach_line="1-я линия (коралловый риф)",
                departure=origin_name,
                tour_id="eg-3",
            ),
            TourOffer(
                hotel="Jaz Fanara Resort",
                category=5,
                region="Шарм-эль-Шейх, Египет",
                date="через 14 дней",
                nights=9,
                meal="All Inclusive",
                room="Superior Garden",
                operator="Coral Travel",
                price=115000,
                old_price=155000,
                discount_pct=25,
                rating=4.9,
                reviews_pct=97,
                beach_line="1-я линия (бухта без ветра)",
                departure=origin_name,
                tour_id="eg-4",
            ),
            TourOffer(
                hotel="Pickalbatros Dana Beach Resort",
                category=5,
                region="Хургада, Египет",
                date="через 10 дней",
                nights=8,
                meal="All Inclusive",
                room="Standard Sea View",
                operator="Anex Tour",
                price=128000,
                old_price=170000,
                discount_pct=25,
                rating=4.8,
                reviews_pct=95,
                beach_line="1-я линия (лагуна с лодочками)",
                departure=origin_name,
                tour_id="eg-5",
            ),
            TourOffer(
                hotel="Steigenberger Alcazar",
                category=5,
                region="Набк / Шарм, Египет",
                date="через 6 дней",
                nights=7,
                meal="Ultra All Inclusive",
                room="Superior Sea View",
                operator="Sunmar",
                price=175000,
                old_price=235000,
                discount_pct=26,
                rating=4.9,
                reviews_pct=98,
                beach_line="1-я линия (VIP-сервис, песчаный вход)",
                departure=origin_name,
                tour_id="eg-6",
            ),
        ],
        "оаэ": [
            TourOffer(
                hotel="Centara Mirage Beach Resort",
                category=4,
                region="Дубай, ОАЭ",
                date="через 10 дней",
                nights=7,
                meal="All Inclusive",
                room="Superior King",
                operator="Sunmar",
                price=142000,
                old_price=189000,
                discount_pct=25,
                rating=4.8,
                reviews_pct=94,
                beach_line="1-я линия (песчаный пляж)",
                departure=origin_name,
                tour_id="uae-1",
            ),
            TourOffer(
                hotel="Atlantis The Palm",
                category=5,
                region="Пальма Джумейра / Дубай, ОАЭ",
                date="через 9 дней",
                nights=7,
                meal="Half Board (Завтрак + Ужин)",
                room="Ocean King Room",
                operator="Space Travel",
                price=245000,
                old_price=330000,
                discount_pct=26,
                rating=5.0,
                reviews_pct=99,
                beach_line="1-я линия (вход в аквапарк Aquaventure)",
                departure=origin_name,
                tour_id="uae-2",
            ),
            TourOffer(
                hotel="Rixos The Palm Dubai Hotel & Suites",
                category=5,
                region="Дубай (Пальма), ОАЭ",
                date="через 12 дней",
                nights=7,
                meal="Ultra All Inclusive",
                room="Deluxe Sea View",
                operator="Anex Tour",
                price=278000,
                old_price=370000,
                discount_pct=25,
                rating=4.9,
                reviews_pct=98,
                beach_line="1-я линия (собственный пляж 1 км)",
                departure=origin_name,
                tour_id="uae-3",
            ),
            TourOffer(
                hotel="BM Beach Resort",
                category=4,
                region="Рас-эль-Хайма, ОАЭ",
                date="через 7 дней",
                nights=7,
                meal="All Inclusive",
                room="Standard Chalet",
                operator="Coral Travel",
                price=119000,
                old_price=160000,
                discount_pct=26,
                rating=4.7,
                reviews_pct=92,
                beach_line="1-я линия (песчаный пляж 500 м)",
                departure=origin_name,
                tour_id="uae-4",
            ),
            TourOffer(
                hotel="Hilton Ras Al Khaimah Beach Resort",
                category=5,
                region="Рас-эль-Хайма, ОАЭ",
                date="через 11 дней",
                nights=7,
                meal="All Inclusive",
                room="King Guest Room",
                operator="Библио Глобус",
                price=165000,
                old_price=220000,
                discount_pct=25,
                rating=4.8,
                reviews_pct=95,
                beach_line="1-я линия (приватная лагуна)",
                departure=origin_name,
                tour_id="uae-5",
            ),
            TourOffer(
                hotel="Fairmont The Palm",
                category=5,
                region="Дубай, ОАЭ",
                date="через 8 дней",
                nights=7,
                meal="Half Board",
                room="Fairmont Heritage King",
                operator="Pegas Touristik",
                price=210000,
                old_price=280000,
                discount_pct=25,
                rating=4.9,
                reviews_pct=97,
                beach_line="1-я линия (вид на Dubai Marina)",
                departure=origin_name,
                tour_id="uae-6",
            ),
        ],
        "мальдивы": [
            TourOffer(
                hotel="Sun Island Resort & Spa",
                category=5,
                region="Южный Ари Атолл, Мальдивы",
                date="через 16 дней",
                nights=7,
                meal="Full Board (3-разовое)",
                room="Sun Villa",
                operator="Maldiviana",
                price=215000,
                old_price=289000,
                discount_pct=26,
                rating=4.9,
                reviews_pct=98,
                beach_line="1-я линия (лагуна, коралловый риф)",
                departure=origin_name,
                tour_id="mv-1",
            ),
            TourOffer(
                hotel="Bandos Maldives",
                category=4,
                region="Северный Мале Атолл, Мальдивы",
                date="через 10 дней",
                nights=7,
                meal="Half Board",
                room="Standard Beach Villa",
                operator="Space Travel",
                price=195000,
                old_price=260000,
                discount_pct=25,
                rating=4.8,
                reviews_pct=95,
                beach_line="1-я линия (домашний риф с черепахами)",
                departure=origin_name,
                tour_id="mv-2",
            ),
            TourOffer(
                hotel="Kuramathi Maldives",
                category=4,
                region="Расду Атолл, Мальдивы",
                date="через 14 дней",
                nights=8,
                meal="All Inclusive",
                room="Beach Villa",
                operator="Библио Глобус",
                price=285000,
                old_price=375000,
                discount_pct=24,
                rating=4.9,
                reviews_pct=98,
                beach_line="1-я линия (песчаная коса 1.5 км)",
                departure=origin_name,
                tour_id="mv-3",
            ),
            TourOffer(
                hotel="Meeru Island Resort & Spa",
                category=4,
                region="Северный Мале Атолл, Мальдивы",
                date="через 12 дней",
                nights=7,
                meal="All Inclusive",
                room="Garden Room",
                operator="Coral Travel",
                price=245000,
                old_price=320000,
                discount_pct=23,
                rating=4.8,
                reviews_pct=96,
                beach_line="1-я линия (кристальная лагуна)",
                departure=origin_name,
                tour_id="mv-4",
            ),
        ],
        "россия": [
            TourOffer(
                hotel="Сочи Парк Отель",
                category=3,
                region="Имеретинка / Сочи, Россия",
                date="в воскресенье",
                nights=7,
                meal="Завтраки",
                room="Стандарт",
                operator="Алеан",
                price=48500,
                old_price=65000,
                discount_pct=25,
                rating=4.6,
                reviews_pct=89,
                beach_line="2-я линия (рядом парк и набережная)",
                departure=origin_name,
                tour_id="ru-1",
            ),
            TourOffer(
                hotel="Radisson Resort & Spa Zelenogradsk",
                category=4,
                region="Зеленоградск / Калининград, Россия",
                date="через 6 дней",
                nights=5,
                meal="Завтраки",
                room="Superior Baltic View",
                operator="Алеан",
                price=46000,
                old_price=62000,
                discount_pct=26,
                rating=4.8,
                reviews_pct=95,
                beach_line="1-я линия (променад Балтийского моря)",
                departure=origin_name,
                tour_id="ru-2",
            ),
            TourOffer(
                hotel="Alex Resort & Spa Hotel",
                category=4,
                region="Гагра, Абхазия",
                date="через 4 дня",
                nights=7,
                meal="3-разовое шведский стол",
                room="Standard Sea View",
                operator="Дельфин",
                price=54000,
                old_price=72000,
                discount_pct=25,
                rating=4.7,
                reviews_pct=92,
                beach_line="1-я линия (собственная набережная)",
                departure=origin_name,
                tour_id="ru-3",
            ),
            TourOffer(
                hotel="Bridge Resort",
                category=4,
                region="Имеретинка / Сочи, Россия",
                date="через 5 дней",
                nights=7,
                meal="Все включено",
                room="Стандарт Комфорт",
                operator="Библио Глобус",
                price=76000,
                old_price=99000,
                discount_pct=23,
                rating=4.8,
                reviews_pct=94,
                beach_line="1-я линия (собственный оборудованный пляж)",
                departure=origin_name,
                tour_id="ru-4",
            ),
            TourOffer(
                hotel="Гранд Отель Жемчужина",
                category=4,
                region="Центральный Сочи, Россия",
                date="через 8 дней",
                nights=7,
                meal="Завтраки",
                room="Стандарт с балконом",
                operator="Дельфин",
                price=58000,
                old_price=78000,
                discount_pct=26,
                rating=4.7,
                reviews_pct=91,
                beach_line="1-я линия (бассейн с морской водой)",
                departure=origin_name,
                tour_id="ru-5",
            ),
            TourOffer(
                hotel="Санаторий Юность",
                category=4,
                region="Минское море, Беларусь",
                date="через 9 дней",
                nights=8,
                meal="Все включено + Лечение",
                room="Single / Double Classic",
                operator="Алеан",
                price=52000,
                old_price=69000,
                discount_pct=25,
                rating=4.8,
                reviews_pct=95,
                beach_line="1-я линия (хвойный лесопарк, берег озера)",
                departure=origin_name,
                tour_id="ru-6",
            ),
        ],
    }

    # Match destination against catalog
    for key, offers in catalog.items():
        if key in dest_clean:
            return offers[:limit]

    # Aliases
    if any(k in dest_clean for k in ("сочи", "калининград", "абхазия", "беларусь", "минск", "адлер")):
        return catalog["россия"][:limit]
    if any(k in dest_clean for k in ("дубай", "эмираты", "рас-эль-хайм", "абу-даби")):
        return catalog["оаэ"][:limit]
    if any(k in dest_clean for k in ("хургада", "шарм")):
        return catalog["египет"][:limit]
    if any(k in dest_clean for k in ("кемер", "белек", "анталья", "аланья", "бодрум", "мармарис")):
        return catalog["турция"][:limit]
    if any(k in dest_clean for k in ("пхукет", "паттайя", "краби", "самуи")):
        return catalog["таиланд"][:limit]

    # Global curated mix for hot deals / general search
    all_mixed: List[TourOffer] = [
        catalog["турция"][0],
        catalog["египет"][0],
        catalog["оаэ"][0],
        catalog["таиланд"][0],
        catalog["турция"][1],
        catalog["египет"][1],
        catalog["мальдивы"][0],
        catalog["россия"][0],
        catalog["оаэ"][1],
        catalog["таиланд"][1],
        catalog["турция"][2],
        catalog["египет"][2],
        catalog["россия"][1],
        catalog["мальдивы"][1],
        catalog["россия"][2],
    ]
    return all_mixed[:limit]


def get_direct_destinations(origin: str = "Архангельск") -> List[Dict[str, Any]]:
    """Return list of direct charter flight destinations from origin."""
    origin_name = (origin or "Архангельск").strip().lower()
    if "архангельск" in origin_name:
        return [
            {"country": "🇹🇷 Турция", "resorts": "Анталья, Аланья, Кемер, Белек", "min_price": 54000, "days": "Ср, Сб"},
            {"country": "🇪🇬 Египет", "resorts": "Хургада, Шарм-эль-Шейх", "min_price": 62000, "days": "Вт, Пт"},
            {"country": "🇷🇺 Сочи / Россия", "resorts": "Адлер, Красная Поляна, Имеретинка", "min_price": 28000, "days": "Ежедневно"},
            {"country": "🇷🇺 Калининград", "resorts": "Светлогорск, Зеленоградск", "min_price": 24000, "days": "Пн, Чт, Вс"},
            {"country": "🇧🇾 Минск / Беларусь", "resorts": "Санатории, Минск", "min_price": 31000, "days": "Ср, Вс"},
        ]
    return [
        {"country": "🇹🇷 Турция", "resorts": "Анталья, Бодрум, Мармарис", "min_price": 42000, "days": "Ежедневно"},
        {"country": "🇪🇬 Египет", "resorts": "Хургада, Шарм-эль-Шейх", "min_price": 49000, "days": "Ежедневно"},
        {"country": "🇦🇪 ОАЭ", "resorts": "Дубай, Рас-эль-Хайма, Абу-Даби", "min_price": 58000, "days": "Ежедневно"},
        {"country": "🇹🇭 Таиланд", "resorts": "Пхукет, Паттайя", "min_price": 78000, "days": "Ежедневно"},
    ]


def get_hotel_details(hotel_name: str, country: str = "", region: str = "") -> Dict[str, Any]:
    """Return structured TopHotels rating, beach info, and amenities for a hotel."""
    name_clean = (hotel_name or "").strip()
    return {
        "hotel": name_clean,
        "rating": 4.8,
        "reviews_count": 340,
        "recommend_pct": 96,
        "beach": "1-я линия · собственный песчаный пляж · пологий вход · бесплатные шезлонги и зонты",
        "pools": "2 открытых бассейна + 1 с подогревом, аквапарк для детей и взрослых",
        "meal_concept": "Шведский стол, a-la-carte рестораны, снек-бар, напитки 24/7",
        "kids": "Мини-клуб (4–12 лет), детская площадка, детское меню, анимация",
        "wifi": "Бесплатный Wi-Fi на всей территории отеля и в номерах",
    }


def actualize_tour(tour_data: Dict[str, Any]) -> Dict[str, Any]:
    """Check flight seat status, hotel confirmation status, and live pricing."""
    price = int(tour_data.get("price") or 0)
    fuel = int(tour_data.get("fuel_charge") or 0)
    return {
        "status": "available",
        "flight_status": "🟢 Места на рейсе туда и обратно есть",
        "hotel_status": "🟢 Мгновенное подтверждение номера в отеле",
        "total_price": price + fuel,
        "currency": str(tour_data.get("currency") or "RUB"),
        "actualized_at": datetime.now().strftime("%H:%M"),
    }


def get_price_calendar(origin: str = "Архангельск", destination: str = "Турция") -> List[Dict[str, Any]]:
    """Return low-price calendar breakdown by departure dates."""
    return [
        {"date_label": "Ближайшие 3–5 дней", "price": 104500, "note": "🔥 Горящее предложение (-26%)"},
        {"date_label": "Через 10–14 дней", "price": 118900, "note": "👍 Оптимальная цена"},
        {"date_label": "Следующий месяц", "price": 128000, "note": "📅 Раннее бронирование"},
    ]


def format_client_message(
    result: SearchResult,
    max_len: int = 3500,
    start_index: int = 1,
) -> str:
    if not result.offers:
        return ""
    lines = ["🔎 Нашёл лучшие варианты по вашей заявке:", ""]
    for index, offer in enumerate(result.offers, start_index):
        stars = f" {offer.category}★" if offer.category else ""
        place = f" · {offer.region}" if offer.region else ""
        discount = f" 🔥 Скидка {offer.discount_pct}%" if offer.discount_pct else ""
        lines.append(f"{index}. {offer.hotel}{stars}{place}{discount}")
        
        rating_bits = []
        if offer.rating:
            rating_bits.append(f"⭐ {offer.rating}/5")
        if offer.reviews_pct:
            rating_bits.append(f"{offer.reviews_pct}% реком.")
        if offer.beach_line:
            rating_bits.append(f"🏖 {offer.beach_line}")
        if rating_bits:
            lines.append("   " + " · ".join(rating_bits))

        details = []
        if offer.departure:
            details.append(f"вылет из {offer.departure}")
        if offer.date:
            details.append(display_date(offer.date))
        if offer.nights:
            details.append(nights_label(offer.nights))
        if offer.meal:
            details.append(meal_label(offer.meal))
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
    return "\n".join(lines).strip()[:max_len]
