from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import Enum

from .identity import IdentityStatus, ProviderHotelRef


class DegradationCode(str, Enum):
    IDENTITY_UNMAPPED = "identity_unmapped"
    IDENTITY_CONFLICT = "identity_conflict"
    PRICE_MISSING = "price_missing"
    PRICE_PROVIDER_TIMEOUT = "price_provider_timeout"
    PRICE_PROVIDER_UNAVAILABLE = "price_provider_unavailable"
    INVALID_PRICE = "invalid_price"
    CURRENCY_MISMATCH = "currency_mismatch"
    DATES_MISMATCH = "dates_mismatch"
    OCCUPANCY_MISMATCH = "occupancy_mismatch"


class CandidateEligibility(str, Enum):
    ELIGIBLE = "eligible"
    EXCLUDED = "excluded"


class PipelineDegradationLevel(str, Enum):
    HEALTHY = "healthy"
    PARTIAL = "partial"
    BLOCKED = "blocked"


@dataclass(frozen=True)
class CandidateDegradation:
    canonical_hotel_id: str | None
    provider_refs: tuple[ProviderHotelRef, ...]
    eligibility: CandidateEligibility
    codes: tuple[DegradationCode, ...]
    details: tuple[str, ...] = ()


@dataclass(frozen=True)
class ProviderDegradation:
    provider: str
    code: DegradationCode
    affected_candidates: int


@dataclass(frozen=True)
class PricingRequestContext:
    check_in: date
    check_out: date
    adults: int
    children: int
    currency: str


@dataclass(frozen=True)
class PriceJoinFacts:
    total_price: int | None
    currency: str | None
    check_in: date
    check_out: date
    adults: int
    children: int


def identity_code(status: IdentityStatus) -> DegradationCode | None:
    if status == IdentityStatus.UNMAPPED:
        return DegradationCode.IDENTITY_UNMAPPED
    if status in (IdentityStatus.CONFLICT, IdentityStatus.AMBIGUOUS):
        return DegradationCode.IDENTITY_CONFLICT
    return None


def evaluate_candidate(
    *,
    canonical_hotel_id: str | None,
    provider_refs: tuple[ProviderHotelRef, ...],
    identity_status: IdentityStatus,
    request: PricingRequestContext,
    pricing: PriceJoinFacts | None,
) -> CandidateDegradation:
    codes: list[DegradationCode] = []
    details: list[str] = []

    identity_failure = identity_code(identity_status)
    if identity_failure is not None:
        codes.append(identity_failure)
        details.append(f"identity_status={identity_status.value}")

    if pricing is None:
        if identity_failure is None:
            codes.append(DegradationCode.PRICE_MISSING)
            details.append("No price returned for matched hotel")
    else:
        if pricing.total_price is None:
            codes.append(DegradationCode.PRICE_MISSING)
            details.append("Price provider returned no total_price")
        elif pricing.total_price <= 0:
            codes.append(DegradationCode.INVALID_PRICE)
            details.append("total_price must be positive")

        if pricing.currency != request.currency:
            codes.append(DegradationCode.CURRENCY_MISMATCH)
            details.append(f"expected currency={request.currency}, got={pricing.currency}")

        if pricing.check_in != request.check_in or pricing.check_out != request.check_out:
            codes.append(DegradationCode.DATES_MISMATCH)
            details.append("Pricing dates do not match request")

        if pricing.adults != request.adults or pricing.children != request.children:
            codes.append(DegradationCode.OCCUPANCY_MISMATCH)
            details.append("Pricing occupancy does not match request")

    deduped_codes = tuple(dict.fromkeys(codes))
    return CandidateDegradation(
        canonical_hotel_id=canonical_hotel_id,
        provider_refs=provider_refs,
        eligibility=(
            CandidateEligibility.ELIGIBLE
            if not deduped_codes
            else CandidateEligibility.EXCLUDED
        ),
        codes=deduped_codes,
        details=tuple(details),
    )


def pipeline_level(eligible_count: int, required_top_n: int = 3) -> PipelineDegradationLevel:
    if eligible_count <= 0:
        return PipelineDegradationLevel.BLOCKED
    if eligible_count < required_top_n:
        return PipelineDegradationLevel.PARTIAL
    return PipelineDegradationLevel.HEALTHY
