"""Read-only Alean SAPI 2.3 adapter; booking and SOAP confirmation are excluded.

Contract: AleanSearchAPI.DOCX supplied by Alean on 2026-08-07. Use the
documented XML representation rather than guessing the JSON envelope.
"""
from __future__ import annotations

import hashlib
import logging
import os
import re
import threading
import time
import xml.etree.ElementTree as ET
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import date, datetime

import requests

from shared import tourvisor as tv

ENDPOINT = "https://sapi.alean.ru:3443/services"
_CATALOGS = {
    "GetCountries": "country", "GetDepartCities": "city", "GetResorts": "resort",
    "GetHotels": "hotel", "GetHotelCategories": "hotelCategory",
}
_cache = OrderedDict()
_lock = threading.Lock()


@dataclass
class AleanSettings:
    enabled: bool = False
    username: str = field(default="", repr=False)
    password: str = field(default="", repr=False)
    timeout: float = 10
    max_offers: int = 15

    @classmethod
    def from_env(cls):
        # Credentials alone never activate a new supplier in production.
        return cls(
            enabled=os.getenv("ALEAN_ENABLED", "false").lower() in ("true", "1", "yes"),
            username=os.getenv("ALEAN_USERNAME", "").strip(),
            password=os.getenv("ALEAN_PASSWORD", ""),
        )


def _request(settings, session, action, params=None):
    if action not in _CATALOGS and action != "GetTours":
        raise ValueError("Unsupported Alean operation")
    with session.get(
        ENDPOINT, params={"action": action, **(params or {})},
        auth=(settings.username, settings.password),
        headers={"Accept": "application/xml"}, timeout=settings.timeout,
        allow_redirects=False, stream=True,
    ) as response:
        response.raise_for_status()
        if response.status_code != 200:
            raise ValueError("Unexpected Alean status")
        chunks, size = [], 0
        for chunk in response.iter_content(65536):
            size += len(chunk)
            if size > 16 * 1024 * 1024:
                raise ValueError("Alean response too large")
            chunks.append(chunk)
    raw = b"".join(chunks)
    # Reject declarations before parsing; no expansion or external entities.
    if b"<!DOCTYPE" in raw.upper() or b"<!ENTITY" in raw.upper() or b"\x00" in raw:
        raise ValueError("Unsupported XML declaration")
    root = ET.fromstring(raw)
    expected = action[0].lower() + action[1:] + "Result"
    if root.tag != expected:
        raise ValueError("Unexpected Alean response")
    return root


def _catalog(settings, session, action):
    account = hashlib.sha256((settings.username + "\0" + settings.password).encode()).digest()
    key = (account, action)
    with _lock:
        cached = _cache.get(key)
        if cached and time.monotonic() - cached[0] < 86400:
            return cached[1]
        root = _request(settings, session, action)
        rows = [dict(node.attrib) for node in root.findall(_CATALOGS[action])]
        _cache[key] = (time.monotonic(), rows)
        _cache.move_to_end(key)
        while len(_cache) > 32:
            _cache.popitem(last=False)
        return rows


def _named(rows, name):
    normalized = " ".join(str(name or "").casefold().replace("ё", "е").split())
    matches = [r for r in rows if " ".join(r.get("name", "").casefold().replace("ё", "е").split()) == normalized]
    # Never silently select a different or ambiguous city/country.
    return matches[0] if len(matches) == 1 and normalized else None


def search_tours(settings, session, info, *, log=None):
    log = log or logging.getLogger(__name__)
    if not settings.enabled or not settings.username or not settings.password:
        return tv.SearchResult(error="Alean сейчас не настроен")
    if info.get("direct_only") or info.get("needs_consultation"):
        return tv.SearchResult(error="Для этих условий Alean требуется подбор менеджером")
    try:
        raw_dates = str(info.get("dates") or "").strip()
        raw_nights = str(info.get("nights") or "7").strip()
        window = tv.resolve_search_window(
            raw_dates,
            nights_raw=info.get("nights") if info.get("dates_are_trip") is False else None,
        )
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw_dates):
            exact = date.fromisoformat(raw_dates)
            night_match = re.fullmatch(r"(\d{1,2})(?:\s*[-–]\s*(\d{1,2}))?", raw_nights)
            if night_match and exact >= date.today():
                low, high = int(night_match[1]), int(night_match[2] or night_match[1])
                if 1 <= low <= high <= 28:
                    window = tv.SearchWindow(exact.isoformat(),exact.isoformat(),low,high)
        if not window:
            return tv.SearchResult(error="Для Alean нужны точные даты")
        start, end = date.fromisoformat(window.date_from), date.fromisoformat(window.date_to)
        if (end - start).days > 3 or window.nights_to - window.nights_from > 3:
            return tv.SearchResult(error="Для Alean сузьте диапазоны заезда и ночей до четырёх значений")
        adult_match = re.fullmatch(r"\s*(\d+)(?:\s+взросл\w*)?\s*", str(info.get("people") or ""))
        ages = info.get("kids_ages") or []
        if not adult_match or not 1 <= int(adult_match[1]) <= 10 or not isinstance(ages, list):
            return tv.SearchResult(error="Для Alean уточните состав туристов")
        adults = int(adult_match[1])
        # Hotel children keep their actual ages, including 12–17-year-olds.
        if len(ages) > 6 or any(type(age) is not int or not 0 <= age <= 17 for age in ages):
            return tv.SearchResult(error="Для Alean уточните возраст каждого ребёнка")
        budget = int(info.get("budget") or 0)
        cap = budget if info.get("budget_scope") == "total" else budget * (adults + len(ages))
        if info.get("budget_open_ended"):
            cap = 0

        countries = _catalog(settings, session, "GetCountries")
        resorts = _catalog(settings, session, "GetResorts")
        country = _named(countries, info.get("destination"))
        resort = None if country else _named(resorts, info.get("destination"))
        if resort:
            country = next((r for r in countries if r.get("id") == resort.get("countryId")), None)
        departure = _named(_catalog(settings, session, "GetDepartCities"), info.get("origin"))
        if not country or not country.get("cid") or not departure or not departure.get("cid"):
            return tv.SearchResult(error="Направление или город вылета не найден в Alean")
        params = {
            "countryCID": country["cid"], "departCityCID": departure["cid"],
            "dateFrom": start.strftime("%d.%m.%Y"), "dateTo": end.strftime("%d.%m.%Y"),
            "nightsMin": window.nights_from, "nightsMax": window.nights_to,
            "adults": adults, "kids": len(ages), "currencyCode": "RUB",
            "count": min(999, max(1, settings.max_offers)),
            "ticketsIncluded": 1, "hasTickets": 1, "excludeHotelsByRequest": 1,
        }
        if resort:
            if not resort.get("cid"):
                return tv.SearchResult(error="Курорт Alean не имеет идентификатора")
            params["resortCIDs"] = resort["cid"]
        if ages:
            params["kidsAges"] = ",".join(map(str, ages))
        if cap > 0:
            params["priceMax"] = cap
        hotels = _catalog(settings, session, "GetHotels")
        if info.get("hotel_query"):
            selected = [h for h in hotels if tv._hotel_matches(h.get("name", ""), str(info["hotel_query"])) and h.get("cid")]
            if not selected:
                return tv.SearchResult(error="Отель не найден в Alean")
            params["hotelCIDs"] = ",".join(h["cid"] for h in selected)
        categories = {r.get("id"): r.get("name", "") for r in _catalog(settings, session, "GetHotelCategories")}
        hotel_map = {r.get("id"): r for r in hotels}
        resort_map = {r.get("id"): r for r in resorts}
        root = _request(settings, session, "GetTours", params)
        offers, seen = [], set()
        for node in root.findall("tour"):
            row = node.attrib
            try:
                price, nights = int(row["price"]), int(row["nights"])
                day = datetime.strptime(row["tourDate"], "%d.%m.%Y").date()
            except (KeyError, ValueError):
                continue
            hotel = hotel_map.get(row.get("hotelId"))
            if not hotel or not hotel.get("name") or price <= 0 or (cap > 0 and price > cap):
                continue
            if not start <= day <= end or not window.nights_from <= nights <= window.nights_to:
                continue
            if row.get("ticketsIncluded") != "1" or row.get("hotelIsInStop") != "0":
                continue
            if not any(row.get("has" + cls + "TicketsDpt") == "1" and row.get("has" + cls + "TicketsRtn") == "1" for cls in ("Econom", "Business")):
                continue
            location = resort_map.get(row.get("resortId"), {})
            if location.get("countryId") != country["id"] or (resort and location.get("id") != resort["id"]):
                continue
            if info.get("hotel_query") and not tv._hotel_matches(hotel["name"], str(info["hotel_query"])):
                continue
            category = re.search(r"[1-5]", categories.get(row.get("hotelCategoryId"), ""))
            # offerId is a surrogate, not a booking identifier. Stable within this card only.
            identity = hashlib.sha256(repr(sorted(row.items())).encode()).hexdigest()[:24]
            if identity in seen:
                continue
            seen.add(identity)
            offers.append(tv.TourOffer(
                hotel=hotel["name"], category=int(category[0]) if category else 0,
                region=location.get("name", ""), date=day.isoformat(), nights=nights,
                meal=row.get("mealCode", ""), room=row.get("roomTypeName", ""),
                operator="Алеан", price=price, currency="RUB", provider="alean",
                departure=departure["name"], tour_id="alean:" + identity,
            ))
        return tv.SearchResult(offers=sorted(offers, key=lambda o: o.price)[:settings.max_offers])
    except requests.HTTPError as exc:
        status = exc.response.status_code if exc.response is not None else 0
        log.warning("Alean HTTP status %s", status)
        return tv.SearchResult(error=f"Alean недоступен (HTTP {status})")
    except requests.Timeout:
        return tv.SearchResult(error="Alean: timeout")
    except Exception as exc:
        # URLs, Basic auth and upstream XML must never enter logs.
        log.warning("Alean search failed (%s)", type(exc).__name__)
        return tv.SearchResult(error="Alean временно не ответил или вернул некорректные данные")


def actualize_tour(offer):
    result = tv.actualize_tour(offer)
    result["hotel_status"] = "🟡 Менеджеру нужно проверить цену и наличие через Alean SOAP Specify"
    return result
