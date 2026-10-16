"""Read-only Alean SAPI 2.3 adapter; booking and SOAP confirmation are excluded.

Contract: AleanSearchAPI.DOCX supplied by Alean on 2026-08-07. Use the
documented XML representation rather than guessing the JSON envelope.
"""
from __future__ import annotations

import hashlib
import logging
import math
import os
import re
import secrets
import threading
import time
import xml.etree.ElementTree as ET
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation

import requests
from urllib3.util import Timeout

from shared import tourvisor as tv

ENDPOINT = "https://sapi.alean.ru:3443/services"
_CATALOGS = {
    "GetCountries": "country", "GetDepartCities": "city", "GetResorts": "resort",
    "GetHotels": "hotel", "GetHotelCategories": "hotelCategory",
}
_cache = OrderedDict()
_lock = threading.Lock()
_cache_salt = secrets.token_bytes(32)
# Group membership verified against Alean's public Sochi hotel hierarchy and
# the SAPI names on 2026-10-12. Other districts remain unaccepted until verified.
# https://www.alean.ru/hotels/rossiya/krasnodarskiy_kray/sochi_gorod-kurort/
_SOCHI_MEMBERS = ("Адлер", "Лазаревское", "Мамайка", "Сочи центр")


@dataclass
class AleanSettings:
    enabled: bool = False
    username: str = field(default="", repr=False)
    password: str = field(default="", repr=False)
    timeout: float = 10
    max_offers: int = 15
    search_timeout: float = 30
    range_timeout: float = 60
    total_timeout: float | None = field(default=None, repr=False)

    @classmethod
    def from_env(cls):
        try:
            search_timeout = float(os.getenv("ALEAN_SEARCH_TIMEOUT_SECONDS", "30"))
        except ValueError:
            search_timeout = 30
        if not math.isfinite(search_timeout) or not 10 <= search_timeout <= 60:
            search_timeout = 30
        try:
            range_timeout = float(os.getenv('ALEAN_RANGE_TIMEOUT_SECONDS', '60'))
        except ValueError:
            range_timeout = 60
        if not math.isfinite(range_timeout) or not 30 <= range_timeout <= 120:
            range_timeout = 60
        # Credentials alone never activate a new supplier in production.
        return cls(
            enabled=os.getenv("ALEAN_ENABLED", "false").lower() in ("true", "1", "yes"),
            username=os.getenv("ALEAN_USERNAME", "").strip(),
            password=os.getenv("ALEAN_PASSWORD", ""),
            search_timeout=search_timeout,
            range_timeout=range_timeout,
        )


def _request(settings, session, action, params=None):
    if action not in _CATALOGS and action != "GetTours":
        raise ValueError("Unsupported Alean operation")
    with session.get(
        ENDPOINT, params={"action": action, **(params or {})},
        auth=(settings.username, settings.password),
        headers={"Accept": "application/xml"},
        # GetTours has taken >14 seconds in authenticated checks. Keep the
        # connection/catalog limit short while allowing the search to respond.
        timeout=(Timeout(total=settings.total_timeout, connect=min(settings.timeout, settings.total_timeout),
                         read=min(settings.search_timeout, settings.total_timeout)) if settings.total_timeout is not None
                 else (settings.timeout, settings.search_timeout)) if action == "GetTours" else settings.timeout,
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


def _account_key(settings):
    # Cache keys retain no raw credentials. A process-local salt and a slow KDF
    # prevent these account fingerprints from becoming reusable password hashes.
    return hashlib.pbkdf2_hmac(
        "sha256", (settings.username + "\0" + settings.password).encode(),
        _cache_salt, 600_000,
    )


def _catalog(settings, session, action, *, account=None):
    key = (account if account is not None else _account_key(settings), action)
    with _lock:
        cached = _cache.get(key)
        if cached and time.monotonic() - cached[0] < 86400:
            return cached[1]
    # Outbound I/O must not serialize unrelated searches during supplier outages.
    root = _request(settings, session, action)
    rows = [dict(node.attrib) for node in root.findall(_CATALOGS[action])]
    with _lock:
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


def _allowed_resort_ids(rows, resort, country):
    allowed = {resort["id"]}
    if resort.get("name") != "Сочи" or country.get("name") != "Россия":
        return allowed
    local = [row for row in rows if row.get("countryId") == country["id"]]
    for name in _SOCHI_MEMBERS:
        member = _named(local, name)
        if member and member.get("id") and member.get("cid"):
            allowed.add(member["id"])
    return allowed


def _resolve_destination(countries, resorts, destination):
    country = _named(countries, destination)
    resort = None if country else _named(resorts, destination)
    if resort:
        country = next((row for row in countries if row.get('id') == resort.get('countryId')), None)
    if country or resort:
        return country, resort
    parts = [part.strip() for part in str(destination or '').split(',')]
    if len(parts) != 2 or not all(parts):
        return None, None
    country = _named(countries, parts[1])
    if not country:
        return None, None
    resort = _named([row for row in resorts if row.get('countryId') == country.get('id')], parts[0])
    return (country, resort) if resort else (None, None)


def search_tours(settings, session, info, *, log=None, cancelled=None, on_progress=None):
    log = log or logging.getLogger(__name__)
    if not settings.enabled or not settings.username or not settings.password:
        return tv.SearchResult(error="Alean сейчас не настроен")
    if info.get("direct_only") or info.get("needs_consultation"):
        return tv.SearchResult(error="Для этих условий Alean требуется подбор менеджером")
    try:
        raw_dates = str(info.get("dates") or "").strip()
        raw_nights = str(info["nights"] if info.get("nights") is not None else "7").strip()
        night_match = re.fullmatch(r"(\d{1,2})(?:\s*[-–]\s*(\d{1,2}))?(?:\s*(?:ночь|ночи|ночей))?", raw_nights)
        if info.get("dates_are_trip") is False or re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw_dates):
            if not night_match or not 1 <= int(night_match[1]) <= int(night_match[2] or night_match[1]) <= 28:
                return tv.SearchResult(error="Для Alean укажите от 1 до 28 ночей")
        window = tv.resolve_search_window(
            raw_dates,
            nights_raw=info.get("nights") if info.get("dates_are_trip") is False else None,
        )
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw_dates):
            exact = date.fromisoformat(raw_dates)
            if night_match and exact >= date.today():
                low, high = int(night_match[1]), int(night_match[2] or night_match[1])
                if 1 <= low <= high <= 28:
                    window = tv.SearchWindow(exact.isoformat(),exact.isoformat(),low,high)
        if not window:
            return tv.SearchResult(error="Для Alean нужны точные даты", error_code="search_window")
        start, end = date.fromisoformat(window.date_from), date.fromisoformat(window.date_to)
        if (end - start).days > 30:
            return tv.SearchResult(error="Укажите диапазон вылета не более 31 дня", error_code="search_window")
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
        if budget < 0 or cap > 2147483647:
            return tv.SearchResult(error="Для Alean уточните бюджет")
        if info.get("budget_open_ended"):
            cap = 0

        # Derive once per search, rather than repeating the KDF for every catalog.
        account = _account_key(settings)
        def catalog(action):
            if cancelled and cancelled():
                raise InterruptedError("Search cancelled")
            return _catalog(settings, session, action, account=account)
        countries = catalog("GetCountries")
        resorts = catalog("GetResorts")
        country, resort = _resolve_destination(countries, resorts, info.get("destination"))
        departure = _named(catalog("GetDepartCities"), info.get("origin"))
        if not country or not country.get("cid"):
            return tv.SearchResult(error="Направление не найдено в Alean", error_code="destination_unsupported")
        if not departure or not departure.get("cid"):
            return tv.SearchResult(error="Город вылета не найден в Alean", error_code="departure_unsupported")
        allowed_resorts = _allowed_resort_ids(resorts, resort, country) if resort else None
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
        hotels = catalog("GetHotels")
        if info.get("hotel_query"):
            selected = [h for h in hotels if tv._hotel_matches(h.get("name", ""), str(info["hotel_query"])) and h.get("cid")]
            if not selected:
                return tv.SearchResult(error="Отель не найден в Alean")
            params["hotelCIDs"] = ",".join(h["cid"] for h in selected)
        categories = {r.get("id"): r.get("name", "") for r in catalog("GetHotelCategories")}
        hotel_map = {r.get("id"): r for r in hotels}
        resort_map = {r.get("id"): r for r in resorts}
        # SAPI accepts at most four arrival dates and four durations per call.
        # Cover the whole requested rectangle, without widening either bound.
        windows = [(day, min(day + timedelta(days=3), end), low, min(low + 3, window.nights_to))
                   for day in (start + timedelta(days=i) for i in range(0, (end-start).days + 1, 4))
                   for low in range(window.nights_from, window.nights_to + 1, 4)]
        root = ET.Element("getToursResult")
        completed = 0
        deadline = time.monotonic() + settings.range_timeout
        local = threading.local()
        transports, transports_lock = [], threading.Lock()
        def run_window(bounds):
            if cancelled and cancelled():
                raise InterruptedError('Search cancelled')
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise requests.Timeout('Range budget exhausted')
            day_from, day_to, night_from, night_to = bounds
            query = dict(params, dateFrom=day_from.strftime('%d.%m.%Y'), dateTo=day_to.strftime('%d.%m.%Y'),
                         nightsMin=night_from, nightsMax=night_to)
            transport = session
            if len(windows) > 1 and isinstance(session, requests.Session):
                # Separate Session per worker: share no mutable cookies or retry
                # adapters. Preserve routing/TLS configuration and never mutate
                # the process-wide Telegram/VK HTTP session.
                if not hasattr(local, 'session'):
                    transport = requests.Session()
                    transport.headers.update(session.headers)
                    transport.proxies.update(session.proxies)
                    transport.verify, transport.cert, transport.trust_env = session.verify, session.cert, session.trust_env
                    local.session = transport
                    with transports_lock:
                        transports.append(transport)
                transport = local.session
            config = settings if len(windows) == 1 else replace(settings, total_timeout=remaining)
            return _request(config, transport, 'GetTours', query)
        def snapshot():
            offers, seen = [], set()
            for node in root.findall("tour"):
                row = node.attrib
                try:
                    supplier_price, nights = Decimal(row["price"]), int(row["nights"])
                    day = datetime.strptime(row["tourDate"], "%d.%m.%Y").date()
                except (KeyError, ValueError, InvalidOperation):
                    continue
                hotel = hotel_map.get(row.get("hotelId"))
                if not supplier_price.is_finite() or not hotel or not hotel.get("name"):
                    continue
                if supplier_price <= 0 or (cap > 0 and supplier_price > cap):
                    continue
                numeric_price = float(supplier_price)
                if not math.isfinite(numeric_price) or numeric_price <= 0:
                    continue
                # Retain kopecks while keeping the card JSON serializable. Budget
                # comparisons above use the original decimal, before float conversion.
                price = int(supplier_price) if supplier_price == supplier_price.to_integral_value() else numeric_price
                if not start <= day <= end or not window.nights_from <= nights <= window.nights_to:
                    continue
                if row.get("ticketsIncluded") != "1" or row.get("hotelIsInStop") != "0":
                    continue
                if not any(row.get("has" + cls + "TicketsDpt") == "1" and row.get("has" + cls + "TicketsRtn") == "1" for cls in ("Econom", "Business")):
                    continue
                location = resort_map.get(row.get("resortId"), {})
                if location.get("countryId") != country["id"] or (allowed_resorts is not None and location.get("id") not in allowed_resorts):
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
            # Local completion marker, never a supplier token. VK uses it to offer
            # an explicit above-budget retry after a successful empty search.
            partial = completed < len(windows)
            warning = (f"Проверена часть диапазона: {completed} из {len(windows)} окон. "
                       "Результаты неполные. Повторите поиск или сузьте даты.") if partial else ""
            return tv.SearchResult(offers=sorted(offers, key=lambda o: o.price)[:settings.max_offers], search_id=-1,
                                   partial=partial, completed_windows=completed, total_windows=len(windows), warning=warning,
                                   error=warning if partial and not offers else "", error_code="partial" if partial else "")

        if len(windows) == 1:
            root.extend(run_window(windows[0]).findall('tour'))
            completed = 1
        else:
            pending = set()
            next_index, stopped = 0, False
            try:
                with ThreadPoolExecutor(max_workers=3, thread_name_prefix='alean-range') as pool:
                    while pending or next_index < len(windows):
                        if (cancelled and cancelled()) or time.monotonic() >= deadline:
                            stopped = True
                        while not stopped and len(pending) < 3 and next_index < len(windows):
                            pending.add(pool.submit(run_window, windows[next_index]))
                            next_index += 1
                        if not pending:
                            break
                        done, pending = wait(pending, timeout=.25, return_when=FIRST_COMPLETED)
                        for future in done:
                            try:
                                root.extend(future.result().findall('tour'))
                                completed += 1
                            except Exception as exc:
                                log.warning('Alean range window failed (%s)', type(exc).__name__)
                                if isinstance(exc, InterruptedError) or (isinstance(exc, requests.HTTPError)
                                        and exc.response is not None and 400 <= exc.response.status_code < 500):
                                    stopped = True
                        if done and on_progress and not (cancelled and cancelled()):
                            try:
                                on_progress(snapshot())
                            except Exception as exc:
                                log.warning('Alean progress callback failed (%s)', type(exc).__name__)
                        if stopped:
                            for future in pending:
                                future.cancel()
                            # Drain already in-flight requests before closing
                            # their worker-owned connections; no new work starts.
                            if not pending:
                                break
            finally:
                for transport in transports:
                    transport.close()
            if cancelled and cancelled():
                return tv.SearchResult(error='Поиск отменён', error_code='cancelled')
        return snapshot()
    except InterruptedError:
        return tv.SearchResult(error="Поиск отменён", error_code="cancelled")
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
    # The shared fallback already preserves kopecks and rejects invalid amounts.
    # Do not overwrite its safe result with unchecked stored quote arithmetic.
    result["hotel_status"] = "🟡 Менеджеру нужно проверить цену и наличие через Alean SOAP Specify"
    return result
