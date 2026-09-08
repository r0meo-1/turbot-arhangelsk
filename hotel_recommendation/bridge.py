from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Iterable

from .degradation import (
    CandidateDegradation,
    DegradationCode,
    PipelineDegradationLevel,
    PriceJoinFacts,
    PricingRequestContext,
    ProviderDegradation,
    evaluate_candidate,
    pipeline_level,
)
from .feature_extraction import FeatureRequest, HotelFeatureFacts, extract_signals
from .identity import CanonicalIdentityResolver, IdentityStatus, ProviderHotelRef
from .models import ScoredHotel
from .scoring import score_candidate
from .selection import select_top_three


@dataclass(frozen=True)
class BridgeRequest:
    feature_request: FeatureRequest
    check_in: date
    check_out: date

    @property
    def pricing_context(self) -> PricingRequestContext:
        return PricingRequestContext(
            check_in=self.check_in,
            check_out=self.check_out,
            adults=self.feature_request.adults,
            children=len(self.feature_request.children_ages),
            currency=self.feature_request.currency,
        )


@dataclass(frozen=True)
class OfflineCandidateFacts:
    content_ref: ProviderHotelRef
    pricing_ref: ProviderHotelRef | None
    name: str
    total_price: int | None
    currency: str | None
    price_check_in: date
    price_check_out: date
    price_adults: int
    price_children: int
    beach_distance_m: int
    tripadvisor_rating: float
    tripadvisor_review_count: int
    service_rating: float
    family_friendly: bool
    breakfast: bool
    amenities: frozenset[str] = frozenset()


@dataclass(frozen=True)
class OfflineBridgeResult:
    level: PipelineDegradationLevel
    eligible_ids: tuple[str, ...]
    exclusions: tuple[CandidateDegradation, ...]
    scored: tuple[ScoredHotel, ...]
    top_three: tuple[tuple[str, ScoredHotel], ...]
    provider_degradation: ProviderDegradation | None = None


def _provider_outage_result(
    *,
    candidates: tuple[OfflineCandidateFacts, ...],
    provider: str,
    code: DegradationCode,
) -> OfflineBridgeResult:
    return OfflineBridgeResult(
        level=PipelineDegradationLevel.BLOCKED,
        eligible_ids=(),
        exclusions=(),
        scored=(),
        top_three=(),
        provider_degradation=ProviderDegradation(
            provider=provider,
            code=code,
            affected_candidates=len(candidates),
        ),
    )


def run_offline_bridge(
    *,
    request: BridgeRequest,
    candidates: Iterable[OfflineCandidateFacts],
    identity_resolver: CanonicalIdentityResolver,
    price_provider_outage: DegradationCode | None = None,
    price_provider_name: str = "commercial_price",
) -> OfflineBridgeResult:
    """Run the deterministic M2C bridge without network access or AI.

    Flow: canonical identity -> price join validation/degradation -> feature
    extraction -> hotel-v1.0 scoring -> deterministic TOP-3 selection.
    """

    frozen_candidates = tuple(candidates)
    if price_provider_outage is not None:
        if price_provider_outage not in {
            DegradationCode.PRICE_PROVIDER_TIMEOUT,
            DegradationCode.PRICE_PROVIDER_UNAVAILABLE,
        }:
            raise ValueError("price_provider_outage must be a provider-level degradation code")
        return _provider_outage_result(
            candidates=frozen_candidates,
            provider=price_provider_name,
            code=price_provider_outage,
        )

    scored: list[ScoredHotel] = []
    eligible_ids: list[str] = []
    exclusions: list[CandidateDegradation] = []

    for candidate in frozen_candidates:
        content_resolution = identity_resolver.resolve(candidate.content_ref)
        canonical_id = content_resolution.canonical_hotel_id
        identity_status = content_resolution.status
        refs = [candidate.content_ref]

        pricing_resolution = None
        if candidate.pricing_ref is not None:
            refs.append(candidate.pricing_ref)
            pricing_resolution = identity_resolver.resolve(candidate.pricing_ref)
            if identity_status == IdentityStatus.MATCHED:
                if pricing_resolution.status != IdentityStatus.MATCHED:
                    identity_status = pricing_resolution.status
                elif pricing_resolution.canonical_hotel_id != canonical_id:
                    identity_status = IdentityStatus.CONFLICT

        pricing: PriceJoinFacts | None = None
        if candidate.pricing_ref is not None and pricing_resolution is not None:
            if pricing_resolution.status == IdentityStatus.MATCHED and pricing_resolution.canonical_hotel_id == canonical_id:
                pricing = PriceJoinFacts(
                    total_price=candidate.total_price,
                    currency=candidate.currency,
                    check_in=candidate.price_check_in,
                    check_out=candidate.price_check_out,
                    adults=candidate.price_adults,
                    children=candidate.price_children,
                )

        degradation = evaluate_candidate(
            canonical_hotel_id=canonical_id,
            provider_refs=tuple(refs),
            identity_status=identity_status,
            request=request.pricing_context,
            pricing=pricing,
        )
        if degradation.codes:
            exclusions.append(degradation)
            continue

        assert canonical_id is not None
        assert candidate.total_price is not None
        assert candidate.currency is not None

        feature_result = extract_signals(
            request.feature_request,
            HotelFeatureFacts(
                canonical_hotel_id=canonical_id,
                total_price=candidate.total_price,
                currency=candidate.currency,
                beach_distance_m=candidate.beach_distance_m,
                tripadvisor_rating=candidate.tripadvisor_rating,
                tripadvisor_review_count=candidate.tripadvisor_review_count,
                service_rating=candidate.service_rating,
                family_friendly=candidate.family_friendly,
                breakfast=candidate.breakfast,
                amenities=candidate.amenities,
            ),
        )

        hotel = {
            "hotel_id": canonical_id,
            "name": candidate.name,
            "price_total": candidate.total_price,
            "currency": candidate.currency,
            "tripadvisor_rating": candidate.tripadvisor_rating,
            "tripadvisor_review_count": candidate.tripadvisor_review_count,
            "signals": dict(feature_result.signals),
            "feature_rules_version": feature_result.rules_version,
        }
        scored_hotel = score_candidate(hotel)
        scored.append(scored_hotel)
        eligible_ids.append(canonical_id)

    selected = select_top_three(scored)
    return OfflineBridgeResult(
        level=pipeline_level(len(scored)),
        eligible_ids=tuple(eligible_ids),
        exclusions=tuple(exclusions),
        scored=tuple(scored),
        top_three=tuple(selected),
    )
