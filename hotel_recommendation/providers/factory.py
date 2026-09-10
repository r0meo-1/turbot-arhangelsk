"""Explicit opt-in wiring for hotel facts, outside the frozen VS0 pipeline."""

from __future__ import annotations

import os
from typing import NoReturn

from .base import HotelFacts, HotelFactsProvider, HotelFactsSearchResult
from .tripadvisor import TripadvisorConfigurationError, TripadvisorTerraClient


class DisabledHotelFactsProvider:
    """No network or synthetic facts when runtime API access is unavailable."""

    @staticmethod
    def _unavailable() -> NoReturn:
        raise TripadvisorConfigurationError(
            "Hotel facts provider is disabled; configure TRIPADVISOR_PROVIDER=terra "
            "and a runtime TRIPADVISOR_API_KEY to enable it"
        )

    def search_hotels(
        self, destination: str, *, limit: int = 20, locale: str = "ru-RU"
    ) -> HotelFactsSearchResult:
        self._unavailable()

    def get_location(self, location_id: str, *, locale: str = "ru-RU") -> HotelFacts:
        self._unavailable()

    def get_photos(
        self, location_id: str, *, limit: int = 5, locale: str = "ru-RU"
    ) -> tuple[str, ...]:
        self._unavailable()


def create_hotel_facts_provider() -> HotelFactsProvider:
    """Construct the selected provider without making a network request.

    A key alone does not enable live access. Existing direct Terra callers,
    including the explicit M2B live smoke, retain their original behavior.
    """
    mode = os.getenv("TRIPADVISOR_PROVIDER", "disabled").strip().lower()
    if mode == "disabled":
        return DisabledHotelFactsProvider()
    if mode == "terra":
        return TripadvisorTerraClient()
    raise TripadvisorConfigurationError(
        "TRIPADVISOR_PROVIDER must be disabled or terra"
    )
