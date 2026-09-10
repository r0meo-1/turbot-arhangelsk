# M2 — Tripadvisor Terra Adapter

Status: implementation scaffold for real Tripadvisor facts provider.

## ChatGPT plugin versus bot runtime

The connected Tripadvisor plugin exposes session tools (`search_hotels`,
`hotel_details`, `compare_hotels`). It does not expose a reusable bot credential
or a deployment endpoint through those tools. A working plugin connection is
therefore not proof of runtime API access. Do not embed session identifiers or
attempt to replay the plugin's private transport in the Telegram/VK bot.

Official Terra access is separate and depends on the account's package and
endpoint authorization. References checked on 2026-09-10:

- https://docs.terra.tripadvisor.com/docs/overview
- https://docs.terra.tripadvisor.com/docs/api-access-and-limits

This change does not certify a live API connection. The existing M2B live smoke
remains the explicit verification gate once a runtime key is provisioned.

## Optional provider entry point

Use the existing `HotelFactsProvider` protocol at an application composition
boundary, outside the frozen deterministic pipeline:

```python
from hotel_recommendation.providers import (
    HotelFactsProvider,
    TripadvisorProviderError,
    create_hotel_facts_provider,
)

provider: HotelFactsProvider = create_hotel_facts_provider()
try:
    facts = provider.search_hotels("Phuket", limit=10)
except TripadvisorProviderError:
    # Application boundary: report facts unavailable; do not fabricate hotels.
    facts = None
```

`TRIPADVISOR_PROVIDER=disabled` is the factory default, even if a key exists.
The disabled implementation satisfies the same protocol and raises an explicit
configuration error on every operation. It creates no HTTP client and returns
no mock hotels. Set `TRIPADVISOR_PROVIDER=terra` to select the existing adapter.
Unknown modes fail explicitly. Direct `TripadvisorTerraClient` callers and the
existing manual live-smoke command retain their behavior.

The factory is intentionally not wired into `bot.py`, `vk_bot.py`, scoring,
the VS0 orchestration, or the M3.1 pricing contract. The existing provider
package is the integration seam; activation in the bot awaits the separate
live-data acceptance gate. No Telegram command, FSM state or dependency is added.

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
TRIPADVISOR_PROVIDER=disabled
TRIPADVISOR_API_KEY=
TRIPADVISOR_BASE_URL=https://terra.tripadvisor.com/api
TRIPADVISOR_TIMEOUT_SECONDS=10
TRIPADVISOR_CACHE_TTL_SECONDS=21600
TRIPADVISOR_CACHE_MAX_ENTRIES=256
```

`TRIPADVISOR_API_KEY` is the only required value for live calls. The remaining
values have defaults and are override points for tests or
future migrations.

Timeout must be finite and positive, cache TTL nonnegative, and cache capacity
positive. Invalid numeric env values raise `TripadvisorConfigurationError`.
Keys are excluded from settings repr. Base URLs must use HTTPS without embedded
credentials, queries or fragments; configure only a trusted provider endpoint.
Redirects are rejected to avoid forwarding the API key. HTTP 401/403 raise a
configuration error; 429 and 5xx retain the existing typed degradation behavior.
Malformed collection envelopes and invalid JSON raise `TripadvisorResponseError`
instead of silently appearing as an empty successful search.

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
