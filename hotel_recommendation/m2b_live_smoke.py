from __future__ import annotations

from dataclasses import dataclass

from .providers import HotelFactsSearchResult, TripadvisorTerraClient


FORBIDDEN_PROVIDER_FIELDS = (
    "price_total",
    "signals",
    "beach_distance_m",
    "family_friendly",
    "breakfast",
)


class LiveSmokeValidationError(RuntimeError):
    """Raised when the live Terra response does not satisfy the M2B gate."""


@dataclass(frozen=True)
class LiveSmokeReport:
    destination: str
    provider: str
    provider_request_id: str
    hotel_count: int
    fetched_at: str
    stale_cache: bool


def validate_live_search_result(result: HotelFactsSearchResult) -> LiveSmokeReport:
    """Validate the strict M2B acceptance gate against a live search result.

    A cached fallback is deliberately rejected: M2B exists to prove that the
    Terra gateway itself answered successfully, not merely that old data can be
    replayed from cache.
    """

    if result.provider != "tripadvisor":
        raise LiveSmokeValidationError(f"unexpected provider: {result.provider!r}")
    if result.stale_cache:
        raise LiveSmokeValidationError("stale cache cannot satisfy M2B live verification")
    if not result.provider_request_id.strip():
        raise LiveSmokeValidationError("provider_request_id is missing")
    if not result.hotels:
        raise LiveSmokeValidationError("Tripadvisor returned an empty hotel list")

    for index, hotel in enumerate(result.hotels):
        prefix = f"hotel[{index}]"
        if not hotel.hotel_id.strip():
            raise LiveSmokeValidationError(f"{prefix}: hotel_id is missing")
        if not hotel.hotel_name.strip():
            raise LiveSmokeValidationError(f"{prefix}: hotel_name is missing")
        if hotel.tripadvisor_rating is None:
            raise LiveSmokeValidationError(f"{prefix}: rating is missing")
        if not 0 <= hotel.tripadvisor_rating <= 5:
            raise LiveSmokeValidationError(f"{prefix}: rating is outside 0..5")
        if hotel.tripadvisor_review_count < 0:
            raise LiveSmokeValidationError(f"{prefix}: review_count is negative")

        for field in FORBIDDEN_PROVIDER_FIELDS:
            if hasattr(hotel, field):
                raise LiveSmokeValidationError(
                    f"{prefix}: provider leaked synthetic/commercial field {field!r}"
                )

    return LiveSmokeReport(
        destination=result.destination,
        provider=result.provider,
        provider_request_id=result.provider_request_id,
        hotel_count=len(result.hotels),
        fetched_at=result.fetched_at,
        stale_cache=result.stale_cache,
    )


def run_live_smoke(
    destination: str = "Phuket",
    *,
    limit: int = 10,
    locale: str = "en-US",
) -> LiveSmokeReport:
    client = TripadvisorTerraClient()
    result = client.search_hotels(destination, limit=limit, locale=locale)
    return validate_live_search_result(result)
