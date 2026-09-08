from __future__ import annotations

from datetime import datetime, timezone
from typing import Mapping, Sequence

from ..identity import ProviderHotelRef
from .base import HotelPriceQuote, HotelPriceSearchResult, PriceSearchRequest


class StaticPriceProvider:
    """Deterministic offline provider used by contract and bridge tests.

    Missing requested hotels stay missing. This adapter never manufactures a
    quote from budget, averages, defaults, or another hotel's price.
    """

    def __init__(
        self,
        *,
        provider_name: str,
        quotes: Mapping[ProviderHotelRef, HotelPriceQuote],
        provider_request_id: str = "fixture-price-request",
        fetched_at: datetime | None = None,
    ) -> None:
        self.provider_name = provider_name.strip()
        if not self.provider_name:
            raise ValueError("provider_name is required")
        self._quotes = dict(quotes)
        self._provider_request_id = provider_request_id
        self._fetched_at = fetched_at or datetime(2030, 1, 1, tzinfo=timezone.utc)

        for ref, quote in self._quotes.items():
            if ref != quote.provider_ref:
                raise ValueError("quote mapping key must equal quote.provider_ref")
            if ref.provider != self.provider_name:
                raise ValueError("fixture quote provider does not match provider_name")

    def search_prices(
        self,
        request: PriceSearchRequest,
        hotel_refs: Sequence[ProviderHotelRef],
    ) -> HotelPriceSearchResult:
        deduped: list[ProviderHotelRef] = []
        seen: set[ProviderHotelRef] = set()
        for ref in hotel_refs:
            if ref.provider != self.provider_name:
                raise ValueError("requested hotel ref belongs to another provider")
            if ref not in seen:
                seen.add(ref)
                deduped.append(ref)

        found = tuple(self._quotes[ref] for ref in deduped if ref in self._quotes)
        return HotelPriceSearchResult(
            provider=self.provider_name,
            request=request,
            quotes=found,
            fetched_at=self._fetched_at,
            provider_request_id=self._provider_request_id,
            stale_cache=False,
        )
