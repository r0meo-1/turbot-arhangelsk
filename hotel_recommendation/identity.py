from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping


class IdentityStatus(str, Enum):
    MATCHED = "matched"
    UNMAPPED = "unmapped"
    AMBIGUOUS = "ambiguous"
    CONFLICT = "conflict"


@dataclass(frozen=True)
class ProviderHotelRef:
    provider: str
    provider_hotel_id: str

    def __post_init__(self) -> None:
        if not self.provider.strip():
            raise ValueError("provider is required")
        if not self.provider_hotel_id.strip():
            raise ValueError("provider_hotel_id is required")


@dataclass(frozen=True)
class IdentityResolutionResult:
    provider_ref: ProviderHotelRef
    canonical_hotel_id: str | None
    status: IdentityStatus
    details: str | None = None


class CanonicalIdentityResolver:
    """Deterministic in-memory external-ID resolver.

    Runtime resolution never performs network access, fuzzy matching, geospatial
    matching, or name matching. AMBIGUOUS is reserved for registry validation
    during onboarding and is never emitted by a valid runtime snapshot.
    """

    def __init__(
        self,
        forward_index: Mapping[ProviderHotelRef, str],
        reverse_index: Mapping[str, tuple[ProviderHotelRef, ...]] | None = None,
    ) -> None:
        self._forward = dict(forward_index)
        if reverse_index is None:
            built: dict[str, list[ProviderHotelRef]] = {}
            for ref, canonical_id in self._forward.items():
                built.setdefault(canonical_id, []).append(ref)
            self._reverse = {
                canonical_id: tuple(sorted(refs, key=lambda ref: (ref.provider, ref.provider_hotel_id)))
                for canonical_id, refs in built.items()
            }
        else:
            self._reverse = {key: tuple(value) for key, value in reverse_index.items()}

    def resolve(self, ref: ProviderHotelRef) -> IdentityResolutionResult:
        canonical_id = self._forward.get(ref)
        if canonical_id is None:
            return IdentityResolutionResult(
                provider_ref=ref,
                canonical_hotel_id=None,
                status=IdentityStatus.UNMAPPED,
                details="No mapping present in current identity snapshot",
            )

        if not canonical_id.strip():
            return IdentityResolutionResult(
                provider_ref=ref,
                canonical_hotel_id=None,
                status=IdentityStatus.CONFLICT,
                details="Empty canonical_hotel_id in identity snapshot",
            )

        associated_refs = self._reverse.get(canonical_id, ())
        if ref not in associated_refs:
            return IdentityResolutionResult(
                provider_ref=ref,
                canonical_hotel_id=None,
                status=IdentityStatus.CONFLICT,
                details="Asymmetric index conflict detected",
            )

        return IdentityResolutionResult(
            provider_ref=ref,
            canonical_hotel_id=canonical_id,
            status=IdentityStatus.MATCHED,
        )


def stable_hash(value: Any) -> str:
    data = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(data.encode()).hexdigest()


def deterministic_id(prefix: str, *parts: str) -> str:
    return f"{prefix}_{hashlib.sha256('|'.join(parts).encode()).hexdigest()[:16]}"
