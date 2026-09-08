"""Commercial hotel pricing provider contracts.

Pricing adapters may perform external I/O and return provider-native facts.
They must not assign canonical hotel identities or recommendation scores.
"""

from .base import (
    HotelPriceProvider,
    HotelPriceQuote,
    HotelPriceSearchResult,
    PriceProviderConfigurationError,
    PriceProviderError,
    PriceProviderResponseError,
    PriceProviderTimeoutError,
    PriceProviderUnavailableError,
    PriceSearchRequest,
)
from .static import StaticPriceProvider

__all__ = [
    "HotelPriceProvider",
    "HotelPriceQuote",
    "HotelPriceSearchResult",
    "PriceProviderConfigurationError",
    "PriceProviderError",
    "PriceProviderResponseError",
    "PriceProviderTimeoutError",
    "PriceProviderUnavailableError",
    "PriceSearchRequest",
    "StaticPriceProvider",
]
