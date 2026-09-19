# Package-tour provider router

VK package-tour search no longer depends on one vendor.

## Current order

Default:

```text
Travelata -> Tourvisor
```

Configure with:

```env
TOUR_PROVIDER_ORDER=travelata,tourvisor
TOUR_SEARCH_MAX_OFFERS=15
```

Only providers with complete credentials are considered enabled. The first provider that returns real offers wins. An empty or unavailable provider falls through to the next configured source. Results from different providers are not mixed into a synthetic catalogue.

## Travelata

```env
TRAVELATA_USERNAME=
TRAVELATA_PASSWORD=
# VK_TRAVELATA_ENABLED=true
# TRAVELATA_BASE_URL=https://api-gateway.travelata.ru
# TRAVELATA_TIMEOUT=15
# TRAVELATA_MAX_OFFERS=15
```

Credentials belong only in the server environment. Do not commit them.

## Tourvisor

The existing Tourvisor adapter remains available as a fallback. An expired Tourvisor JWT disables that provider instead of exposing a search button that cannot work.

Tourvisor's current Search API documentation says the JWT is obtained in the travel-agent personal account; no public refresh endpoint is documented. Rotate an expired token in Tourvisor PRO, update `TOURVISOR_TOKEN` in the protected server environment, and restart/redeploy the VK service. `/vk/health` exposes only the safe operational state (`configured`, `enabled`, `token_status`, expiry seconds), never the token itself.

The deploy smoke probe also skips upstream HTTP calls when the JWT is already known to be expired, avoiding a misleading 403 while still reporting `reason=expired_jwt`.

## Safety rules

- Production search never substitutes the curated demo hotel catalogue for failed or empty upstream results.
- If no live provider is configured, the VK review screen does not advertise automatic price search and the user can send the request to a manager.
- Provider outages must never prevent the saved lead from reaching the manager.
- `Level.Travel` should be added only after partner access provides the current API contract. Private endpoints are not guessed.

## Relevant modules

- `shared/tour_providers.py` — provider selection and fallback.
- `shared/travelata.py` — Travelata adapter.
- `shared/tourvisor.py` — Tourvisor adapter and shared normalized offer model.
- `vk_bot.py` — VK UI and background search worker.
- `tests/test_tour_providers.py` — adapter/router regression coverage.
- `tests/test_vk_provider_router_wiring.py` — VK wiring guard.
