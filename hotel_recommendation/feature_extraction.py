from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from .config import WEIGHTS

FEATURE_RULES_VERSION = "hotel-features-v1.0"


class FeatureExtractionError(ValueError):
    """Raised when normalized facts are insufficient or internally invalid."""


@dataclass(frozen=True)
class FeatureRequest:
    budget_total: int
    currency: str
    adults: int
    children_ages: tuple[int, ...] = ()
    preferences: tuple[str, ...] = ()
    max_beach_distance_m: int | None = None


@dataclass(frozen=True)
class HotelFeatureFacts:
    """Normalized factual input. No precomputed score is accepted here."""

    canonical_hotel_id: str
    total_price: int
    currency: str
    beach_distance_m: int
    tripadvisor_rating: float
    tripadvisor_review_count: int
    service_rating: float
    family_friendly: bool
    breakfast: bool
    amenities: frozenset[str] = frozenset()


@dataclass(frozen=True)
class FeatureExtractionResult:
    canonical_hotel_id: str
    rules_version: str
    signals: Mapping[str, int]


def _clamp_score(value: float) -> int:
    return max(0, min(100, round(value)))


def _validate(request: FeatureRequest, facts: HotelFeatureFacts) -> None:
    if not facts.canonical_hotel_id.strip():
        raise FeatureExtractionError("canonical_hotel_id is required")
    if request.budget_total <= 0:
        raise FeatureExtractionError("budget_total must be positive")
    if request.adults <= 0:
        raise FeatureExtractionError("adults must be positive")
    if facts.total_price <= 0:
        raise FeatureExtractionError("total_price must be positive")
    if facts.currency != request.currency:
        raise FeatureExtractionError("currency mismatch must be filtered before feature extraction")
    if facts.beach_distance_m < 0:
        raise FeatureExtractionError("beach_distance_m cannot be negative")
    if not 1.0 <= facts.tripadvisor_rating <= 5.0:
        raise FeatureExtractionError("tripadvisor_rating must be between 1.0 and 5.0")
    if facts.tripadvisor_review_count < 0:
        raise FeatureExtractionError("tripadvisor_review_count cannot be negative")
    if not 1.0 <= facts.service_rating <= 5.0:
        raise FeatureExtractionError("service_rating must be between 1.0 and 5.0")
    if request.max_beach_distance_m is not None and request.max_beach_distance_m < 0:
        raise FeatureExtractionError("max_beach_distance_m cannot be negative")
    if any(age < 0 or age > 17 for age in request.children_ages):
        raise FeatureExtractionError("children ages must be between 0 and 17")


def _budget_score(price: int, budget: int) -> int:
    if price > budget:
        return 0
    # Full score at <=75% of budget. At the budget ceiling the value component
    # is still viable but has no safety headroom, so it bottoms out at 70.
    headroom_ratio = (budget - price) / budget
    return _clamp_score(70 + 30 * min(headroom_ratio / 0.25, 1.0))


def _beach_location_score(distance_m: int) -> int:
    if distance_m <= 100:
        return 100
    if distance_m <= 300:
        return 95
    if distance_m <= 500:
        return 85
    if distance_m <= 1000:
        return 75
    if distance_m <= 1500:
        return 65
    return 0


def _rating_score(rating: float) -> int:
    # Tripadvisor uses a 1–5 scale. Map the observed scale linearly to 0–100.
    return _clamp_score((rating - 1.0) / 4.0 * 100)


def _review_volume_score(review_count: int) -> int:
    if review_count >= 5000:
        return 100
    if review_count >= 2000:
        return 90
    if review_count >= 1000:
        return 80
    if review_count >= 500:
        return 70
    if review_count >= 100:
        return 60
    if review_count > 0:
        return 40
    return 0


def _reviews_score(rating: float, review_count: int) -> int:
    # Reputation quality dominates; review volume only increases confidence.
    return _clamp_score(0.75 * _rating_score(rating) + 0.25 * _review_volume_score(review_count))


def _traveler_fit_score(request: FeatureRequest, facts: HotelFeatureFacts) -> int:
    if request.children_ages:
        return 100 if facts.family_friendly else 0
    return 100


def _available_preferences(request: FeatureRequest, facts: HotelFeatureFacts) -> frozenset[str]:
    available = {item.strip().lower() for item in facts.amenities if item.strip()}
    if facts.breakfast:
        available.add("breakfast")
    if facts.family_friendly:
        available.add("family")
    beach_limit = request.max_beach_distance_m if request.max_beach_distance_m is not None else 1500
    if facts.beach_distance_m <= beach_limit:
        available.add("beach")
    return frozenset(available)


def _preferences_score(request: FeatureRequest, facts: HotelFeatureFacts) -> int:
    requested = tuple(dict.fromkeys(item.strip().lower() for item in request.preferences if item.strip()))
    if not requested:
        return 100
    available = _available_preferences(request, facts)
    matches = sum(1 for item in requested if item in available)
    return _clamp_score(matches / len(requested) * 100)


def extract_signals(request: FeatureRequest, facts: HotelFeatureFacts) -> FeatureExtractionResult:
    """Convert normalized facts into deterministic hotel-v1.0 input signals."""

    _validate(request, facts)
    signals = {
        "budget": _budget_score(facts.total_price, request.budget_total),
        "beach_location": _beach_location_score(facts.beach_distance_m),
        "service": _rating_score(facts.service_rating),
        "reviews": _reviews_score(facts.tripadvisor_rating, facts.tripadvisor_review_count),
        "traveler_fit": _traveler_fit_score(request, facts),
        "preferences": _preferences_score(request, facts),
    }
    if set(signals) != set(WEIGHTS):
        raise FeatureExtractionError("feature keys do not match hotel-v1.0 scoring contract")
    return FeatureExtractionResult(
        canonical_hotel_id=facts.canonical_hotel_id,
        rules_version=FEATURE_RULES_VERSION,
        signals=signals,
    )
