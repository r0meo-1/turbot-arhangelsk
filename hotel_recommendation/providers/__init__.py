"""External hotel-facts providers.

Provider adapters are deliberately outside the deterministic scoring core.
They may perform network I/O and return factual hotel data, but they must not
rank hotels or invent commercial/scoring fields.
"""

from .base import HotelFacts, HotelFactsProvider, HotelFactsSearchResult
from .factory import DisabledHotelFactsProvider, create_hotel_facts_provider
from .tripadvisor import (
    TripadvisorConfigurationError,
    TripadvisorProviderError,
    TripadvisorRateLimitError,
    TripadvisorResponseError,
    TripadvisorTerraClient,
    TripadvisorTerraSettings,
    TripadvisorUnavailableError,
)

__all__ = [
    "DisabledHotelFactsProvider",
    "create_hotel_facts_provider",
    "HotelFacts",
    "HotelFactsProvider",
    "HotelFactsSearchResult",
    "TripadvisorConfigurationError",
    "TripadvisorProviderError",
    "TripadvisorRateLimitError",
    "TripadvisorResponseError",
    "TripadvisorTerraClient",
    "TripadvisorTerraSettings",
    "TripadvisorUnavailableError",
]
