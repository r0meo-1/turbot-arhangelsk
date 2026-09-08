# M2 — Tripadvisor Terra Adapter

Status: implementation scaffold for real Tripadvisor facts provider.

## Why Terra, not the old Content API v1 URL

Current Tripadvisor partner documentation exposes the Terra API under:

```text
https://terra.tripadvisor.com/api
```

Authentication uses the `X-API-Key` request header.

The adapter currently supports:

- `GET /locations/search` with `category=HOTEL`
- `GET /locations/{id}`
- `GET /locations/{id}/photos`
- bounded in-memory TTL cache
- stale-cache fallback on rate limits, upstream 5xx, and network failures
- explicit typed errors when no safe fallback exists

## Environment contract

```ini
TRIPADVISOR_API_KEY=
TRIPADVISOR_BASE_URL=https://terra.tripadvisor.com/api
TRIPADVISOR_TIMEOUT_SECONDS=10
TRIPADVISOR_CACHE_TTL_SECONDS=21600
TRIPADVISOR_CACHE_MAX_ENTRIES=256
```

`TRIPADVISOR_API_KEY` is the only required value for live calls. The remaining
values have production-safe defaults and are override points for tests or
future migrations.

## Architecture boundary

Tripadvisor is a **facts provider**, not a recommender and not a price source.
The adapter returns only factual content such as:

- Tripadvisor location id and localized hotel name
- traveler rating and review count
- coordinates/address
- provider attributes
- representative photo and Tripadvisor URL

It intentionally does **not** emit:

- `price_total`
- `signals`
- `beach_distance_m`
- `family_friendly`
- `breakfast`

Those values require separate deterministic enrichment. In particular, live
bookable hotel pricing belongs to a commerce/pricing provider; Tripadvisor's
Terra content product must not be treated as a hotel-rate API.

Target pipeline after M2:

```text
Tripadvisor Terra facts
        +
commercial / package-tour price facts
        +
deterministic feature extraction
        ↓
existing Hotel Recommendation v1 core
        ↓
TOP-3
```

This keeps the already-verified deterministic core network-free and preserves
its golden contract.
