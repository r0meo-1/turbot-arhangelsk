from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable

import requests

from .base import HotelFacts, HotelFactsSearchResult

logger = logging.getLogger("turbot.hotel_recommendation.tripadvisor")


class TripadvisorProviderError(RuntimeError):
    """Base error for Tripadvisor Terra integration."""


class TripadvisorConfigurationError(TripadvisorProviderError):
    pass


class TripadvisorRateLimitError(TripadvisorProviderError):
    pass


class TripadvisorUnavailableError(TripadvisorProviderError):
    pass


class TripadvisorResponseError(TripadvisorProviderError):
    pass


@dataclass(frozen=True)
class TripadvisorTerraSettings:
    api_key: str
    base_url: str = "https://terra.tripadvisor.com/api"
    timeout_seconds: float = 10.0
    cache_ttl_seconds: int = 21_600
    cache_max_entries: int = 256

    @classmethod
    def from_env(cls) -> "TripadvisorTerraSettings":
        return cls(
            api_key=os.getenv("TRIPADVISOR_API_KEY", "").strip(),
            base_url=os.getenv(
                "TRIPADVISOR_BASE_URL",
                "https://terra.tripadvisor.com/api",
            ).strip().rstrip("/"),
            timeout_seconds=float(os.getenv("TRIPADVISOR_TIMEOUT_SECONDS", "10")),
            cache_ttl_seconds=int(os.getenv("TRIPADVISOR_CACHE_TTL_SECONDS", "21600")),
            cache_max_entries=int(os.getenv("TRIPADVISOR_CACHE_MAX_ENTRIES", "256")),
        )


@dataclass
class _CacheEntry:
    stored_at: float
    value: tuple[Any, str]


class _TTLCache:
    def __init__(self, ttl_seconds: int, max_entries: int, clock: Callable[[], float]) -> None:
        self.ttl_seconds = max(0, ttl_seconds)
        self.max_entries = max(1, max_entries)
        self.clock = clock
        self._data: dict[str, _CacheEntry] = {}
        self._lock = threading.Lock()

    def get(self, key: str, *, allow_stale: bool = False) -> tuple[Any, str] | None:
        with self._lock:
            entry = self._data.get(key)
            if entry is None:
                return None
            is_fresh = (self.clock() - entry.stored_at) <= self.ttl_seconds
            if is_fresh or allow_stale:
                return entry.value
            return None

    def set(self, key: str, value: tuple[Any, str]) -> None:
        with self._lock:
            if len(self._data) >= self.max_entries and key not in self._data:
                oldest = min(self._data, key=lambda item: self._data[item].stored_at)
                self._data.pop(oldest, None)
            self._data[key] = _CacheEntry(stored_at=self.clock(), value=value)


def _translations_value(value: Any, locale: str) -> str:
    if isinstance(value, str):
        return value.strip()
    if not isinstance(value, list):
        return ""
    normalized_locale = locale.replace("_", "-").lower()
    candidates = [item for item in value if isinstance(item, dict)]
    for item in candidates:
        language = str(item.get("language") or "").replace("_", "-").lower()
        if language == normalized_locale and item.get("value"):
            return str(item["value"]).strip()
    for item in candidates:
        if item.get("primary") is True and item.get("value"):
            return str(item["value"]).strip()
    for item in candidates:
        if item.get("value"):
            return str(item["value"]).strip()
    return ""


def _as_float(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_int(value: Any, default: int = 0) -> int:
    if isinstance(value, bool) or value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _rating_and_count(raw: dict[str, Any]) -> tuple[float | None, int]:
    ratings = raw.get("traveler_ratings") or {}
    overall = ratings.get("overall") if isinstance(ratings, dict) else None
    if not isinstance(overall, dict):
        overall = ratings if isinstance(ratings, dict) else {}

    rating = _as_float(
        overall.get("rating")
        or overall.get("value")
        or overall.get("bubble_rating")
        or raw.get("rating")
    )
    count = _as_int(
        overall.get("review_count")
        or overall.get("count")
        or overall.get("num_reviews")
        or raw.get("review_count")
        or raw.get("num_reviews")
    )
    return rating, count


def _coordinates(raw: dict[str, Any]) -> tuple[float | None, float | None]:
    coordinates = raw.get("coordinates") or {}
    if not isinstance(coordinates, dict):
        return None, None
    return (
        _as_float(coordinates.get("latitude") or coordinates.get("lat")),
        _as_float(coordinates.get("longitude") or coordinates.get("lon") or coordinates.get("lng")),
    )


def _address(raw: dict[str, Any], locale: str) -> str:
    addresses = raw.get("addresses")
    if isinstance(addresses, list):
        localized = _translations_value(addresses, locale)
        if localized:
            return localized
        for item in addresses:
            if not isinstance(item, dict):
                continue
            for key in ("formatted", "address", "value"):
                if item.get(key):
                    return str(item[key]).strip()
    if isinstance(addresses, dict):
        for key in ("formatted", "address", "value"):
            if addresses.get(key):
                return str(addresses[key]).strip()
    return ""


def _flatten_attributes(raw: dict[str, Any]) -> tuple[str, ...]:
    attributes = raw.get("attributes") or []
    values: list[str] = []

    def visit(value: Any) -> None:
        if isinstance(value, str):
            text = value.strip()
            if text:
                values.append(text)
        elif isinstance(value, dict):
            for key in ("name", "label", "value"):
                if isinstance(value.get(key), str):
                    visit(value[key])
            for nested_key in ("values", "items", "attributes"):
                if nested_key in value:
                    visit(value[nested_key])
        elif isinstance(value, list):
            for item in value:
                visit(item)

    visit(attributes)
    return tuple(dict.fromkeys(values))


def _first_url(value: Any) -> str:
    if isinstance(value, str) and value.startswith(("http://", "https://")):
        return value
    if isinstance(value, dict):
        for key in ("url", "original", "large", "medium", "small"):
            found = _first_url(value.get(key))
            if found:
                return found
        for child in value.values():
            found = _first_url(child)
            if found:
                return found
    if isinstance(value, list):
        for child in value:
            found = _first_url(child)
            if found:
                return found
    return ""


def _tripadvisor_url(raw: dict[str, Any]) -> str:
    urls = raw.get("urls") or {}
    if isinstance(urls, dict):
        for key in ("tripadvisor", "web", "url"):
            found = _first_url(urls.get(key))
            if found:
                return found
    return _first_url(urls)


def _payload_items(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        for key in ("data", "results", "locations"):
            value = payload.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
    return []


def _to_hotel_facts(raw: dict[str, Any], locale: str) -> HotelFacts:
    hotel_id = raw.get("tripadvisor_id") or raw.get("location_id") or raw.get("id")
    if hotel_id in (None, ""):
        raise TripadvisorResponseError("Tripadvisor location is missing an id")
    name = _translations_value(raw.get("names"), locale) or str(raw.get("name") or "").strip()
    if not name:
        raise TripadvisorResponseError(f"Tripadvisor location {hotel_id} is missing a name")
    rating, review_count = _rating_and_count(raw)
    latitude, longitude = _coordinates(raw)
    return HotelFacts(
        hotel_id=str(hotel_id),
        hotel_name=name,
        tripadvisor_rating=rating,
        tripadvisor_review_count=review_count,
        latitude=latitude,
        longitude=longitude,
        address=_address(raw, locale),
        attributes=_flatten_attributes(raw),
        photo_url=_first_url(raw.get("photos")),
        tripadvisor_url=_tripadvisor_url(raw),
    )


class TripadvisorTerraClient:
    """Tripadvisor Terra adapter that returns facts only.

    It intentionally does not populate ``price_total``, ``signals``,
    ``beach_distance_m``, ``family_friendly`` or ``breakfast``. Those values
    require separate commercial/feature-enrichment stages and must never be
    fabricated from content API data.
    """

    provider_name = "tripadvisor"

    def __init__(
        self,
        settings: TripadvisorTerraSettings | None = None,
        *,
        session: requests.Session | Any | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.settings = settings or TripadvisorTerraSettings.from_env()
        self.session = session or requests.Session()
        self.clock = clock
        self.cache = _TTLCache(
            self.settings.cache_ttl_seconds,
            self.settings.cache_max_entries,
            clock,
        )

    def _ensure_configured(self) -> None:
        if not self.settings.api_key:
            raise TripadvisorConfigurationError(
                "TRIPADVISOR_API_KEY is not configured"
            )
        if not self.settings.base_url.startswith("https://"):
            raise TripadvisorConfigurationError("TRIPADVISOR_BASE_URL must use HTTPS")

    def _request(
        self,
        path: str,
        *,
        params: Any,
        cache_key: str,
    ) -> tuple[Any, str, bool]:
        self._ensure_configured()
        fresh = self.cache.get(cache_key)
        if fresh is not None:
            payload, request_id = fresh
            return payload, request_id, False

        url = f"{self.settings.base_url}{path}"
        try:
            response = self.session.get(
                url,
                params=params,
                timeout=self.settings.timeout_seconds,
                headers={
                    "Accept": "application/json",
                    "X-API-Key": self.settings.api_key,
                },
            )
        except requests.RequestException as exc:
            stale = self.cache.get(cache_key, allow_stale=True)
            if stale is not None:
                payload, request_id = stale
                logger.warning("Tripadvisor unavailable; using stale cache for %s", path)
                return payload, request_id, True
            raise TripadvisorUnavailableError(str(exc)) from exc

        request_id = (
            response.headers.get("x-request-id")
            or response.headers.get("x-trace-id")
            or response.headers.get("trace-id")
            or ""
        )

        if response.status_code == 429:
            stale = self.cache.get(cache_key, allow_stale=True)
            if stale is not None:
                payload, cached_request_id = stale
                logger.warning("Tripadvisor rate limited; using stale cache for %s", path)
                return payload, cached_request_id, True
            raise TripadvisorRateLimitError("Tripadvisor Terra rate limit exceeded")

        if response.status_code >= 500:
            stale = self.cache.get(cache_key, allow_stale=True)
            if stale is not None:
                payload, cached_request_id = stale
                logger.warning("Tripadvisor server error; using stale cache for %s", path)
                return payload, cached_request_id, True
            raise TripadvisorUnavailableError(
                f"Tripadvisor Terra returned HTTP {response.status_code}"
            )

        if response.status_code >= 400:
            raise TripadvisorResponseError(
                f"Tripadvisor Terra returned HTTP {response.status_code}"
            )

        try:
            payload = response.json()
        except ValueError as exc:
            raise TripadvisorResponseError("Tripadvisor Terra returned invalid JSON") from exc

        if not request_id and isinstance(payload, dict):
            request_id = str(payload.get("trace_id") or payload.get("request_id") or "")
        self.cache.set(cache_key, (payload, request_id))
        return payload, request_id, False

    def search_hotels(
        self,
        destination: str,
        *,
        limit: int = 20,
        locale: str = "ru-RU",
    ) -> HotelFactsSearchResult:
        query = (destination or "").strip()
        if not query:
            raise ValueError("destination is required")
        size = max(1, min(int(limit), 20))
        cache_key = f"search:{locale}:{size}:{query.casefold()}"
        payload, request_id, stale = self._request(
            "/locations/search",
            params={
                "query": query,
                "category": "HOTEL",
                "locale": locale,
                "size": size,
            },
            cache_key=cache_key,
        )
        hotels: list[HotelFacts] = []
        for raw in _payload_items(payload):
            try:
                hotels.append(_to_hotel_facts(raw, locale))
            except TripadvisorResponseError as exc:
                logger.warning("Skipping malformed Tripadvisor hotel: %s", exc)
        return HotelFactsSearchResult(
            provider=self.provider_name,
            destination=query,
            fetched_at=datetime.now(timezone.utc).isoformat(),
            provider_request_id=request_id,
            hotels=tuple(hotels[:size]),
            stale_cache=stale,
        )

    def get_location(self, location_id: str, *, locale: str = "ru-RU") -> HotelFacts:
        location_id = str(location_id).strip()
        if not location_id.isdigit() or int(location_id) <= 0:
            raise ValueError("location_id must be a positive integer")
        payload, _, _ = self._request(
            f"/locations/{location_id}",
            params={"locale": locale},
            cache_key=f"location:{locale}:{location_id}",
        )
        if isinstance(payload, dict) and "data" in payload and isinstance(payload["data"], dict):
            payload = payload["data"]
        if not isinstance(payload, dict):
            raise TripadvisorResponseError("Tripadvisor location response is invalid")
        return _to_hotel_facts(payload, locale)

    def get_photos(
        self,
        location_id: str,
        *,
        limit: int = 5,
        locale: str = "ru-RU",
    ) -> tuple[str, ...]:
        location_id = str(location_id).strip()
        if not location_id.isdigit() or int(location_id) <= 0:
            raise ValueError("location_id must be a positive integer")
        size = max(1, min(int(limit), 20))
        payload, _, _ = self._request(
            f"/locations/{location_id}/photos",
            params={"locale": locale, "size": size},
            cache_key=f"photos:{locale}:{size}:{location_id}",
        )
        urls: list[str] = []
        for item in _payload_items(payload):
            url = _first_url(item)
            if url and url not in urls:
                urls.append(url)
        return tuple(urls[:size])
