# M3.1 Commercial Price Provider Contract

Status: **FROZEN**

## Purpose

M3.1 defines the boundary between external commercial pricing APIs and the deterministic hotel recommendation pipeline.

The pricing layer may perform network I/O. It may return commercial facts. It must not rank hotels, invent prices, assign canonical identities, or generate scoring signals.

## Flow

```text
Exact Trip Request
        ↓
Commercial Price Provider
        ↓
Provider-native HotelPriceQuote
        ↓
CanonicalIdentityResolver
        ↓
M2C degradation / feature extraction / scoring
```

## Exact request contract

`PriceSearchRequest` contains:

- `check_in`
- `check_out`
- `adults`
- `children_ages`
- `currency`

Dates and occupancy are exact. A provider may support flexible search internally, but only facts tied to the exact request may enter the deterministic recommendation path.

## Quote contract

`HotelPriceQuote` contains only provider-native commercial facts:

- `provider_ref`
- `total_price`
- `currency`
- `offer_id`

`provider_ref` remains external. The adapter must never create `canonical_hotel_id`.

A missing price is represented by a missing quote or `total_price=None`. It must never be replaced with:

- client budget
- average price
- another hotel's price
- zero
- a guessed/default value

Invalid numeric facts are preserved for downstream degradation. For example, `-5000` must remain `-5000` and become `INVALID_PRICE`; it must not be repaired with `abs()`.

## Search result / trace

`HotelPriceSearchResult` contains:

- provider name
- the exact request echoed back
- provider-native quotes
- `fetched_at`
- optional `provider_request_id`
- `stale_cache`

All quote refs must belong to the declared provider. Duplicate provider hotel refs are rejected.

## Identity boundary

Runtime joins are allowed only through `CanonicalIdentityResolver`.

Forbidden inside price adapters:

- fuzzy hotel-name matching
- geospatial nearest-hotel matching
- assigning canonical IDs from hotel names
- cross-provider ID assumptions

## Tourvisor note

The repository already has `shared/tourvisor.py`, which returns package-tour offers and real commercial prices. However, the current `TourOffer` model preserves the hotel name but not a stable provider hotel ID.

Therefore the existing Tourvisor integration is **not yet eligible** for the strict M3 → M2C canonical join. M3.2 must first prove that Tourvisor exposes and can preserve a stable external hotel ID. Name matching is not an acceptable substitute.

## M3.1 acceptance gate

```text
Same exact request
+ Same fixture provider snapshot
=
Same provider-native quotes
```

And:

```text
Missing provider quote → remains missing
Invalid price → preserved for degradation
Foreign provider ref → rejected
Duplicate provider ref → rejected
canonical_hotel_id → forbidden in adapter output
signals / score → forbidden in adapter output
```

## Next

M3.2: evaluate and, if possible, adapt the existing Tourvisor commercial search into this contract while preserving a stable provider hotel ID and exact request semantics.
