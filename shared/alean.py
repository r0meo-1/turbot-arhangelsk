"""Alean Search API (SAPI) package-tour provider.

The adapter is intentionally read-only and disabled by default.  Alean SAPI
uses HTTP Basic Auth with a dedicated API account.  Credentials stay in the
server environment and are never added to query strings or logs.

The provider-supplied SAPI document describes the XML contract exactly, while
JSON is optional.  We therefore use the explicit /services/xml/ resource to
avoid guessing a JSON serialization shape.
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import threading
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Dict, Iterable, List, Optional

import requests

from shared import tourvisor as _tourvisor


logger = logging.getLogger("turbot.shared.alean")

_DEFAULT_BASE_URL = "https://sapi.alean.ru:3443/services/xml/"
_DEFAULT_CACHE_TTL = 24 * 60 * 60
_DEFAULT_MAX_RESPONSE_BYTES = 12 * 1024 * 1024

_cache_lock = threading.Lock()
_catalog_cache: Dict[str, tuple[float, "_Catalog"]] = {}


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


@dataclass
class AleanSettings:
    enabled: bool = False
    username: str = ""
    password: str = ""
    base_url: str = _DEFAULT_BASE_URL
    timeout: int = 20
    max_offers: int = 15
    dictionary_cache_ttl: int = _DEFAULT_CACHE_TTL
    max_response_bytes: int = _DEFAULT_MAX_RESPONSE_BYTES

    @classmethod
    def from_env(cls) -> "AleanSettings":
        username = os.getenv("ALEAN_SAPI_USERNAME", "").strip()
        password = os.getenv("ALEAN_SAPI_PASSWORD", "").strip()
        # Production remains opt-in even when credentials are present.
        enabled = _env_bool("VK_ALEAN_ENABLED", False)
        return cls(
            enabled=enabled,
            username=username,
            password=password,
            base_url=os.getenv("ALEAN_SAPI_BASE_URL", _DEFAULT_BASE_URL).strip(),
            timeout=max(5, _env_int("ALEAN_SAPI_TIMEOUT", 20)),
            max_offers=max(1, min(100, _env_int("ALEAN_SAPI_MAX_OFFERS", 15))),
            dictionary_cache_ttl=max(
                60, _env_int("ALEAN_SAPI_DICTIONARY_CACHE_TTL", _DEFAULT_CACHE_TTL)
            ),
            max_response_bytes=max(
                64 * 1024,
                _env_int(
                    "ALEAN_SAPI_MAX_RESPONSE_BYTES", _DEFAULT_MAX_RESPONSE_BYTES
                ),
            ),
        )


@dataclass(frozen=True)
class _Catalog:
    countries: tuple[Dict[str, str], ...]
    departures: tuple[Dict[str, str], ...]
    resorts: Dict[str, Dict[str, str]]
    categories: Dict[str, Dict[str, str]]
    hotels: Dict[str, Dict[str, str]]


def _tag_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].casefold()


def _attrs(element: ET.Element) -> Dict[str, str]:
    return {str(key).casefold(): str(value) for key, value in element.attrib.items()}


def _records(root: ET.Element, tag: str) -> List[Dict[str, str]]:
    wanted = tag.casefold()
    return [_attrs(node) for node in root.iter() if _tag_name(node.tag) == wanted]


def _bounded_xml(settings: AleanSettings, response: requests.Response) -> ET.Element:
    status = int(getattr(response, "status_code", 0) or 0)
    if not 200 <= status < 300:
        error = requests.HTTPError(f"Alean SAPI HTTP {status}")
        error.response = response
        raise error

    raw = bytes(getattr(response, "content", b"") or b"")
    if not raw and hasattr(response, "text"):
        raw = str(response.text).encode("utf-8")
    if len(raw) > settings.max_response_bytes:
        raise ValueError("Alean SAPI response exceeds configured size limit")
    if not raw:
        raise ValueError("Alean SAPI returned an empty response")

    try:
        return ET.fromstring(raw)
    except ET.ParseError as exc:
        raise ValueError("Alean SAPI returned invalid XML") from exc


def _request_xml(
    settings: AleanSettings,
    session: requests.Session,
    action: str,
    params: Optional[Dict[str, Any]] = None,
) -> ET.Element:
    query: Dict[str, Any] = {"action": action}
    query.update(params or {})
    response = session.get(
        settings.base_url.rstrip("/") + "/",
        params=query,
        auth=(settings.username, settings.password),
        headers={"Accept": "application/xml"},
        timeout=settings.timeout,
        allow_redirects=False,
    )
    return _bounded_xml(settings, response)


def _normalise_name(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip()).casefold().replace("ё", "е")


def _find_named(rows: Iterable[Dict[str, str]], value: str) -> Optional[Dict[str, str]]:
    wanted = _normalise_name(value)
    if not wanted:
        return None

    exact = None
    fuzzy = None
    for row in rows:
        name = _normalise_name(row.get("name"))
        if name == wanted:
            exact = row
            break
        if wanted in name or name in wanted:
            fuzzy = fuzzy or row
    return exact or fuzzy


def _cache_key(settings: AleanSettings) -> str:
    # Do not retain the username itself in the in-process cache key.
    material = f"{settings.base_url}|{settings.username}".encode("utf-8")
    return hashlib.sha256(material).hexdigest()


def _catalog(settings: AleanSettings, session: requests.Session) -> _Catalog:
    key = _cache_key(settings)
    now = time.monotonic()

    with _cache_lock:
        cached = _catalog_cache.get(key)
        if cached is not None and cached[0] > now:
            return cached[1]

    countries = tuple(_records(_request_xml(settings, session, "GetCountries"), "country"))
    departures = tuple(
        _records(_request_xml(settings, session, "GetDepartCities"), "city")
    )
    resorts = {
        row.get("id", ""): row
        for row in _records(_request_xml(settings, session, "GetResorts"), "resort")
        if row.get("id")
    }
    categories = {
        row.get("id", ""): row
        for row in _records(
            _request_xml(settings, session, "GetHotelCategories"), "hotelCategory"
        )
        if row.get("id")
    }
    hotels = {
        row.get("id", ""): row
        for row in _records(_request_xml(settings, session, "GetHotels"), "hotel")
        if row.get("id")
    }
    catalog = _Catalog(
        countries=countries,
        departures=departures,
        resorts=resorts,
        categories=categories,
        hotels=hotels,
    )
    with _cache_lock:
        _catalog_cache[key] = (now + settings.dictionary_cache_ttl, catalog)
    return catalog


def clear_catalog_cache() -> None:
    """Clear process-local dictionary cache (mainly useful for tests/reloads)."""
    with _cache_lock:
        _catalog_cache.clear()


def readiness_probe(
    settings: AleanSettings,
    session: requests.Session,
) -> Dict[str, Any]:
    """Run the vendor-recommended read-only dictionary smoke.

    Only aggregate counts are returned; response bodies and credentials are not.
    """
    if not settings.username or not settings.password:
        return {"ok": False, "reason": "credentials_missing"}
    try:
        countries = _records(_request_xml(settings, session, "GetCountries"), "country")
        departures = _records(
            _request_xml(settings, session, "GetDepartCities"), "city"
        )
        return {
            "ok": bool(countries and departures),
            "countries": len(countries),
            "depart_cities": len(departures),
        }
    except requests.HTTPError as exc:
        status = getattr(exc.response, "status_code", None)
        return {"ok": False, "reason": "http_error", "status": status}
    except requests.RequestException:
        return {"ok": False, "reason": "transport_error"}
    except Exception:
        return {"ok": False, "reason": "invalid_response"}


def _date_ddmmyyyy(iso_value: str) -> str:
    return datetime.strptime(iso_value, "%Y-%m-%d").strftime("%d.%m.%Y")


def _bounded_dates(date_from: str, date_to: str) -> tuple[str, str]:
    start = datetime.strptime(date_from, "%Y-%m-%d")
    end = datetime.strptime(date_to, "%Y-%m-%d")
    if end < start:
        end = start
    # Provider documentation limits the date range to three days.
    end = min(end, start + timedelta(days=3))
    return start.strftime("%d.%m.%Y"), end.strftime("%d.%m.%Y")


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(float(str(value or "").replace(" ", "").replace(",", ".")))
    except (TypeError, ValueError):
        return default


def _category_value(row: Optional[Dict[str, str]]) -> int:
    if not row:
        return 0
    match = re.search(r"\d+", row.get("name", ""))
    return int(match.group(0)) if match else 0


def _tour_identifier(attrs: Dict[str, str]) -> str:
    material = "|".join(
        [
            attrs.get("tourcid", ""),
            attrs.get("hotelcid", ""),
            attrs.get("tourdate", ""),
            attrs.get("line", ""),
            attrs.get("offerid", ""),
        ]
    ).encode("utf-8")
    return "alean:" + hashlib.sha256(material).hexdigest()[:24]


def _extract_offers(
    root: ET.Element,
    catalog: _Catalog,
    info: Dict[str, Any],
    limit: int,
) -> List[_tourvisor.TourOffer]:
    offers: List[_tourvisor.TourOffer] = []

    for node in root.iter():
        if _tag_name(node.tag) != "tour":
            continue
        row = _attrs(node)
        price = _int(row.get("price"))
        if price <= 0:
            continue

        # Never infer live availability contrary to the provider flags.
        if _int(row.get("hotelisinstop"), 1) != 0:
            continue
        if _int(row.get("ticketsincluded"), 0) == 1:
            economy_ok = (
                _int(row.get("haseconomticketsdpt"), 0) == 1
                and _int(row.get("haseconomticketsrtn"), 0) == 1
            )
            business_ok = (
                _int(row.get("hasbusinessticketsdpt"), 0) == 1
                and _int(row.get("hasbusinessticketsrtn"), 0) == 1
            )
            if not (economy_ok or business_ok):
                continue

        hotel = catalog.hotels.get(row.get("hotelid", ""))
        resort = catalog.resorts.get(row.get("resortid", ""))
        category = catalog.categories.get(row.get("hotelcategoryid", ""))

        hotel_name = (hotel or {}).get("name") or f"Alean hotel {row.get('hotelid', '')}".strip()
        resort_name = (resort or {}).get("name", "")
        offer = _tourvisor.TourOffer(
            hotel=hotel_name,
            category=_category_value(category),
            region=resort_name,
            date=row.get("tourdate") or row.get("checkindate", ""),
            nights=max(1, _int(row.get("nights"), 1)),
            meal=row.get("mealcode", ""),
            room=row.get("roomtypename", "") or row.get("htplacename", ""),
            operator="Alean",
            price=price,
            currency="RUB",
            tour_id=_tour_identifier(row),
            departure=str(info.get("origin") or ""),
        )
        offers.append(offer)

    offers.sort(key=lambda item: item.price)
    unique: List[_tourvisor.TourOffer] = []
    seen: set[str] = set()
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
    settings: AleanSettings,
    session: requests.Session,
    info: Dict[str, Any],
    *,
    log: Optional[logging.Logger] = None,
) -> _tourvisor.SearchResult:
    """Search Alean SAPI and normalize offers into the TurBot contract."""
    log = log or logger
    if not settings.enabled or not settings.username or not settings.password:
        return _tourvisor.SearchResult(error="Alean SAPI сейчас не настроен")
    if info.get("needs_consultation"):
        return _tourvisor.SearchResult(
            error="Для направления нужна консультация менеджера"
        )
    if info.get("direct_only"):
        # The supplied SAPI contract has no direct-flight filter. Falling
        # through to another provider is safer than implying it was applied.
        return _tourvisor.SearchResult(
            error="Alean SAPI не подтверждает фильтр прямого рейса"
        )

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
        catalog = _catalog(settings, session)
        departure = _find_named(catalog.departures, str(info.get("origin") or ""))
        if departure is None:
            return _tourvisor.SearchResult(
                error="Этот город вылета пока не найден в Alean SAPI"
            )
        country = _find_named(catalog.countries, str(info.get("destination") or ""))
        if country is None:
            return _tourvisor.SearchResult(
                error="Это направление пока не найдено в Alean SAPI"
            )

        date_from, date_to = _bounded_dates(window.date_from, window.date_to)
        nights_min = max(1, int(window.nights_from))
        nights_max = max(nights_min, min(int(window.nights_to), nights_min + 3))

        params: Dict[str, Any] = {
            "count": max(1, min(500, settings.max_offers * 4)),
            "dateFrom": date_from,
            "dateTo": date_to,
            "adults": adults,
            "kids": len(child_ages),
            "nightsMin": nights_min,
            "nightsMax": nights_max,
            "currencyCode": "RUB",
            "ticketsIncluded": 1,
            "hasTickets": 1,
            "excludeHotelsByRequest": 1,
            "getLine": 1,
        }
        if country.get("cid"):
            params["countryCID"] = country["cid"]
        else:
            params["countryId"] = country["id"]
        if departure.get("cid"):
            params["departCityCID"] = departure["cid"]
        else:
            params["departCityId"] = departure["id"]
        if child_ages:
            params["kidsAges"] = ",".join(str(age) for age in child_ages)

        try:
            budget = int(info.get("budget") or 0)
        except (TypeError, ValueError):
            budget = 0
        total_cap = 0
        if budget and not info.get("budget_open_ended"):
            total_cap = budget
            if info.get("budget_scope") != "total":
                total_cap *= adults + len(child_ages)
            params["priceMax"] = total_cap

        root = _request_xml(settings, session, "GetTours", params)
        offers = _extract_offers(root, catalog, info, settings.max_offers)
        if total_cap:
            offers = [offer for offer in offers if offer.price <= total_cap]
        if not offers:
            return _tourvisor.SearchResult(error="Подходящих туров пока не найдено")
        return _tourvisor.SearchResult(offers=offers)

    except requests.Timeout:
        return _tourvisor.SearchResult(error="Alean SAPI timeout")
    except requests.HTTPError as exc:
        status = getattr(exc.response, "status_code", None)
        if status in (401, 403):
            log.error("Alean SAPI authorization/access failed: HTTP %s", status)
            return _tourvisor.SearchResult(error=f"Alean SAPI HTTP {status}")
        if status == 429:
            return _tourvisor.SearchResult(error="Alean SAPI HTTP 429")
        log.error("Alean SAPI HTTP failure: %s", status)
        return _tourvisor.SearchResult(
            error=f"Alean SAPI HTTP {status or 'error'}"
        )
    except requests.RequestException as exc:
        log.error("Alean SAPI transport failure: %s", type(exc).__name__)
        return _tourvisor.SearchResult(error="Alean SAPI transport error")
    except Exception as exc:
        log.error("Alean SAPI search failed: %s", type(exc).__name__)
        return _tourvisor.SearchResult(error="Alean SAPI invalid response")
