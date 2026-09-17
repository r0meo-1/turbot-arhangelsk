"""Provider router for package-tour search.

The VK funnel talks to this module instead of a single vendor. Providers are
tried sequentially and never mixed into one synthetic result. An outage or
missing credential therefore falls through to the next configured source.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import List, Optional, Sequence, Tuple

import requests

from shared import sletat as _sletat
from shared import tourvisor as _tourvisor
from shared import travelata as _travelata

logger = logging.getLogger("turbot.shared.tour_providers")


@dataclass
class ProviderSettings:
    order: Sequence[str] = ("travelata", "tourvisor")
    sletat: _sletat.SletatSettings = field(
        default_factory=_sletat.SletatSettings.from_env
    )
    travelata: _travelata.TravelataSettings = field(
        default_factory=_travelata.TravelataSettings
    )
    tourvisor: _tourvisor.TourvisorSettings = field(
        default_factory=_tourvisor.TourvisorSettings
    )

    def enabled_names(self) -> List[str]:
        ordered = [str(raw or "").strip().lower() for raw in self.order if str(raw or "").strip()]
        # Sletat is the preferred Russian package-tour source. Existing VK
        # deployments still default TOUR_PROVIDER_ORDER to travelata,tourvisor,
        # so automatically put Sletat first once its credentials are present.
        if (
            self.sletat.enabled
            and self.sletat.login
            and self.sletat.password
            and "sletat" not in ordered
        ):
            ordered.insert(0, "sletat")

        enabled: List[str] = []
        for name in ordered:
            if name == "sletat":
                if self.sletat.enabled and self.sletat.login and self.sletat.password:
                    enabled.append(name)
            elif name == "travelata":
                if (
                    self.travelata.enabled
                    and self.travelata.username
                    and self.travelata.password
                ):
                    enabled.append(name)
            elif name == "tourvisor":
                if self.tourvisor.enabled and self.tourvisor.token:
                    enabled.append(name)
        return list(dict.fromkeys(enabled))

    @property
    def enabled(self) -> bool:
        return bool(self.enabled_names())


def search_tours(
    settings: ProviderSettings,
    session: requests.Session,
    info: dict,
    *,
    log: Optional[logging.Logger] = None,
) -> Tuple[_tourvisor.SearchResult, str]:
    """Try configured providers in order and return the first useful result.

    A provider returning no offers is not considered fatal: the next provider
    may have different operator inventory. Errors are preserved only if every
    configured source fails or returns an empty result.
    """
    log = log or logger
    errors: List[str] = []
    attempted = False
    last_search_id = None

    for name in settings.enabled_names():
        attempted = True
        if name == "sletat":
            result = _sletat.search_tours(
                settings.sletat, session, info, log=log
            )
        elif name == "travelata":
            result = _travelata.search_tours(
                settings.travelata, session, info, log=log
            )
        elif name == "tourvisor":
            result = _tourvisor.search_tours(
                settings.tourvisor, session, info, log=log
            )
        else:  # defensive; enabled_names() currently filters this already.
            continue

        if result.search_id is not None:
            last_search_id = result.search_id
        if result.offers:
            return result, name
        if result.error:
            errors.append(f"{name}: {result.error}")

    if not attempted:
        return _tourvisor.SearchResult(
            error="Автоматический поиск туров сейчас не настроен"
        ), ""

    return _tourvisor.SearchResult(
        error="; ".join(errors) or "Подходящих туров пока не найдено",
        search_id=last_search_id,
    ), ""
