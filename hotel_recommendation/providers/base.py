from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, Sequence


@dataclass(frozen=True)
class HotelFacts:
    """Factual provider data used before commercial enrichment/scoring."""

    hotel_id: str
    hotel_name: str
    tripadvisor_rating: float | None
    tripadvisor_review_count: int
    latitude: float | None = None
    longitude: float | None = None
    address: str = ""
    attributes: tuple[str, ...] = ()
    photo_url: str = ""
    tripadvisor_url: str = ""


@dataclass(frozen=True)
class HotelFactsSearchResult:
    provider: str
    destination: str
    fetched_at: str
    provider_request_id: str
    hotels: tuple[HotelFacts, ...]
    stale_cache: bool = False


class HotelFactsProvider(Protocol):
    def search_hotels(
        self,
        destination: str,
        *,
        limit: int = 20,
        locale: str = "ru-RU",
    ) -> HotelFactsSearchResult: ...

    def get_location(self, location_id: str, *, locale: str = "ru-RU") -> HotelFacts: ...

    def get_photos(
        self,
        location_id: str,
        *,
        limit: int = 5,
        locale: str = "ru-RU",
    ) -> Sequence[str]: ...
