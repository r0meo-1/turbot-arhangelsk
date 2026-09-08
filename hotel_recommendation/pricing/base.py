from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Protocol, Sequence

from ..identity import ProviderHotelRef


class PriceProviderError(RuntimeError):
    """Base error for commercial pricing integrations."""


class PriceProviderConfigurationError(PriceProviderError):
    pass


class PriceProviderTimeoutError(PriceProviderError):
    pass


class PriceProviderUnavailableError(PriceProviderError):
    pass


class PriceProviderResponseError(PriceProviderError):
    pass


@dataclass(frozen=True)
class PriceSearchRequest:
    """Exact commercial search context.

    The contract is intentionally exact-date and exact-occupancy. Providers may
    internally support flexible windows, but results entering the deterministic
    recommendation pipeline must be tied to one concrete request.
    """

    check_in: date
    check_out: date
    adults: int
    children_ages: tuple[int, ...]
    currency: str

    def __post_init__(self) -> None:
        if self.check_out <= self.check_in:
            raise ValueError("check_out must be after check_in")
        if self.adults < 1:
            raise ValueError("adults must be at least 1")
        if any(isinstance(age, bool) or not isinstance(age, int) or age < 0 or age > 17 for age in self.children_ages):
            raise ValueError("children_ages must contain integers from 0 to 17")
        currency = self.currency.strip().upper()
        if len(currency) != 3 or not currency.isalpha():
            raise ValueError("currency must be a 3-letter ISO-style code")
        object.__setattr__(self, "currency", currency)

    @property
    def children(self) -> int:
        return len(self.children_ages)


@dataclass(frozen=True)
class HotelPriceQuote:
    """One provider-native commercial quote.

    ``provider_ref`` stays external. A price adapter must never assign a
    canonical hotel id itself; canonical identity belongs to the identity
    registry/resolver layer.
    """

    provider_ref: ProviderHotelRef
    total_price: int | None
    currency: str | None
    offer_id: str = ""

    def __post_init__(self) -> None:
        if not self.provider_ref.provider.strip():
            raise ValueError("provider_ref.provider is required")
        if not self.provider_ref.provider_hotel_id.strip():
            raise ValueError("provider_ref.provider_hotel_id is required")
        if self.total_price is not None and (isinstance(self.total_price, bool) or not isinstance(self.total_price, int)):
            raise ValueError("total_price must be an integer or None")
        if self.currency is not None:
            normalized = self.currency.strip().upper()
            if len(normalized) != 3 or not normalized.isalpha():
                raise ValueError("quote currency must be a 3-letter code or None")
            object.__setattr__(self, "currency", normalized)


@dataclass(frozen=True)
class HotelPriceSearchResult:
    """Commercial provider snapshot for one exact request."""

    provider: str
    request: PriceSearchRequest
    quotes: tuple[HotelPriceQuote, ...]
    fetched_at: datetime
    provider_request_id: str | None = None
    stale_cache: bool = False

    def __post_init__(self) -> None:
        provider = self.provider.strip()
        if not provider:
            raise ValueError("provider is required")
        object.__setattr__(self, "provider", provider)

        seen: set[ProviderHotelRef] = set()
        for quote in self.quotes:
            if quote.provider_ref.provider != provider:
                raise ValueError("all quote refs must belong to result.provider")
            if quote.provider_ref in seen:
                raise ValueError("duplicate provider hotel quote")
            seen.add(quote.provider_ref)


class HotelPriceProvider(Protocol):
    provider_name: str

    def search_prices(
        self,
        request: PriceSearchRequest,
        hotel_refs: Sequence[ProviderHotelRef],
    ) -> HotelPriceSearchResult: ...
