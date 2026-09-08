# M2C.3 Degradation Rules

Status: SPEC FROZEN

This document freezes the deterministic degradation contract between canonical hotel identity/pricing joins and feature extraction.

## Scope

M2C.3 does not repair, guess, enrich, rank, or score data. It only:

- evaluates whether a joined candidate is eligible for downstream budget-dependent scoring;
- records deterministic exclusion reasons;
- distinguishes candidate-level absence from provider-level failure;
- produces an aggregate pipeline degradation level and manager-facing audit summary.

The guiding rules are:

```text
UNKNOWN != 0
UNKNOWN != average
UNKNOWN != user budget
provider failed != provider says hotel unavailable
```

No fallback may fabricate a price, identity, feature, signal, or recommendation candidate.

## Identity precondition from M2C.2

Runtime identity resolution is deterministic and does not use name, coordinates, address, fuzzy matching, geo-radius matching, or network calls.

`IdentityStatus.AMBIGUOUS` is an onboarding/snapshot-validation state only. An ambiguous identity registry must not be promoted into production runtime. Runtime resolution therefore permits:

- `MATCHED`
- `UNMAPPED`
- `CONFLICT`

A candidate without a matched canonical identity cannot receive a commercial price join.

```text
No identity = No price join
```

## Degradation codes

```python
from enum import Enum


class DegradationCode(str, Enum):
    # Identity
    IDENTITY_UNMAPPED = "identity_unmapped"
    IDENTITY_CONFLICT = "identity_conflict"

    # Pricing
    PRICE_MISSING = "price_missing"
    PRICE_PROVIDER_TIMEOUT = "price_provider_timeout"
    PRICE_PROVIDER_UNAVAILABLE = "price_provider_unavailable"
    INVALID_PRICE = "invalid_price"

    # Request compatibility
    CURRENCY_MISMATCH = "currency_mismatch"
    DATES_MISMATCH = "dates_mismatch"
    OCCUPANCY_MISMATCH = "occupancy_mismatch"
```

## Candidate eligibility

```python
class CandidateEligibility(str, Enum):
    ELIGIBLE = "eligible"
    EXCLUDED = "excluded"
```

Candidate-level audit record:

```python
from dataclasses import dataclass
from typing import Tuple


@dataclass(frozen=True)
class CandidateDegradation:
    canonical_hotel_id: str | None
    provider_refs: Tuple[ProviderHotelRef, ...]
    eligibility: CandidateEligibility
    codes: Tuple[DegradationCode, ...]
    details: Tuple[str, ...]
```

A candidate may have multiple degradation codes. Evaluation must collect the full deterministic reason set rather than stop after the first error.

Example:

```text
identity matched
price exists
currency mismatch
occupancy mismatch
```

must produce both:

```text
currency_mismatch
occupancy_mismatch
```

## Eligibility invariant

A candidate is eligible if and only if all of the following are true:

```text
identity == MATCHED
AND pricing exists
AND total_price > 0
AND currency is accepted
AND pricing dates exactly match the request
AND pricing occupancy exactly matches the request
```

Equivalent rule:

```python
def is_eligible(result: CandidateDegradation) -> bool:
    return not result.codes
```

M2C.3 never repairs invalid facts. Examples:

- `total_price = -100` -> `INVALID_PRICE`, never `abs(total_price)`;
- request `2 adults + 1 child`, price returned for `2 adults` -> `OCCUPANCY_MISMATCH`;
- missing price -> `PRICE_MISSING`, never substitute average/region/user budget;
- wrong currency -> `CURRENCY_MISMATCH`, never silently convert unless a separately frozen deterministic FX stage exists.

## Candidate-level vs provider-level degradation

A provider outage is not represented as many independent candidate failures.

If the commercial provider successfully responds but omits a specific hotel price:

```text
candidate -> PRICE_MISSING
```

If the provider itself times out or is unavailable:

```python
@dataclass(frozen=True)
class ProviderDegradation:
    provider: str
    code: DegradationCode
    affected_candidates: int
```

Typical codes:

```text
PRICE_PROVIDER_TIMEOUT
PRICE_PROVIDER_UNAVAILABLE
```

This distinction preserves the difference between:

```text
provider failed
```

and:

```text
provider successfully reported no usable price for this hotel
```

## Pipeline degradation level

```python
class PipelineDegradationLevel(str, Enum):
    HEALTHY = "healthy"
    PARTIAL = "partial"
    BLOCKED = "blocked"
```

The level is based on the system's ability to construct the required recommendation set, not on arbitrary exclusion percentages.

For `required_top_n = 3`:

```text
eligible >= 3  -> HEALTHY
eligible 1..2  -> PARTIAL
eligible == 0  -> BLOCKED
```

General invariant:

```text
eligible >= required_top_n -> HEALTHY
0 < eligible < required_top_n -> PARTIAL
eligible == 0 -> BLOCKED
```

## PARTIAL behavior

The system returns only eligible candidates.

```text
2 eligible -> return 2, status PARTIAL
1 eligible -> return 1, status PARTIAL
```

It must not invent a third recommendation, promote a price-less candidate, or bypass the deterministic pipeline merely to preserve a fixed UI card count.

## BLOCKED behavior

When no candidate is eligible:

```text
recommendations = []
pipeline_status = BLOCKED
```

The system must not emit synthetic hotel recommendations.

Manager-facing operational message should explain that no recommendation could be formed with confirmed commercial facts for the request.

## Aggregate audit report

```python
from dataclasses import dataclass
from typing import Mapping, Tuple


@dataclass(frozen=True)
class DegradationReport:
    total_candidates: int
    eligible_candidates: int
    excluded_candidates: int
    level: PipelineDegradationLevel
    exclusions: Tuple[CandidateDegradation, ...]
    provider_degradations: Tuple[ProviderDegradation, ...]
    reason_counts: Mapping[DegradationCode, int]
```

`reason_counts` must be derived deterministically from the candidate and provider degradation records, not maintained as an independent mutable source of truth.

Example manager summary:

```text
Подбор отелей ограничен.

Получено кандидатов: 10
С валидной ценой: 2
Исключено: 8

Причины:
- нет цены: 5
- нет связи идентификаторов: 2
- цена не соответствует составу туристов: 1

Автоматический TOP-3 неполный.
```

For a blocked pipeline:

```text
Автоматическая рекомендация не сформирована.
Нет ни одного кандидата с подтвержденной ценой для параметров заявки.
```

Internal exception traces and credentials must never be exposed in the manager-facing summary.

## Acceptance gate

M2C.3 is accepted only when the following determinism holds:

```text
Same normalized candidates
+ Same request
+ Same provider outcome
=
Same eligibility set
=
Same degradation codes
=
Same provider degradation records
=
Same pipeline degradation level
=
Same manager report
```

Additional invariants:

1. Unknown or invalid commercial facts never become zero/default/average values.
2. A provider outage never masquerades as individual hotel unavailability.
3. `PARTIAL` never pads recommendations with ineligible candidates.
4. `BLOCKED` always yields an empty recommendation list.
5. No scoring or ranking logic exists in this layer.
6. No runtime fuzzy identity resolution exists in this layer.

## Roadmap status

```text
M2C.1 Fact Contracts ............ FROZEN
M2C.2 Canonical Identity ........ FROZEN
M2C.3 Degradation Rules ......... FROZEN
M2C.4 Feature Extraction ........ NEXT
M2C.5 Golden Fixtures ........... PENDING
M2C.6 Offline Bridge E2E ........ PENDING
```

Next artifact: `M2C.4 Deterministic Feature Extraction`, converting validated normalized facts into deterministic `hotel-v1.0` signals without allowing provider data to rank hotels directly.
