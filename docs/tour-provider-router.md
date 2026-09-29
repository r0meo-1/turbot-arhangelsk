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

## Alean SAPI

Alean is implemented as a read-only Search API provider and is deliberately
disabled by default while the remaining vendor rate/use/recheck terms are open.

The adapter uses the provider-documented HTTPS SAPI endpoint with HTTP Basic
Auth and the explicit XML resource. Credentials are sent only in the
Authorization header, never in query strings or logs. Dictionary data is cached
in-process because Alean recommends refreshing dictionaries about once per day.

```env
ALEAN_SAPI_USERNAME=
ALEAN_SAPI_PASSWORD=
# VK_ALEAN_ENABLED=false
# ALEAN_SAPI_BASE_URL=https://sapi.alean.ru:3443/services/xml/
# ALEAN_SAPI_TIMEOUT=20
# ALEAN_SAPI_MAX_OFFERS=15
# ALEAN_SAPI_DICTIONARY_CACHE_TTL=86400
```

The production provider order is unchanged. To opt in only after the production
gate is cleared, include `alean` explicitly in `TOUR_PROVIDER_ORDER` and set
`VK_ALEAN_ENABLED=true`.

For a credentialed, non-PII acceptance smoke:

```bash
python tools/alean_sapi_smoke.py
```

The smoke checks `GetCountries`, `GetDepartCities`, then performs one
synthetic `GetTours` request. It prints only aggregate counts/result status,
never credentials, response bodies, hotel names, or prices.

## Sletat

Sletat uses the official JSON gateway at `https://module.sletat.ru/Main.svc`.
The current production adapter follows the documented flow:
`GetDepartCities → GetCountries → GetTours → GetLoadState → GetTours(updateResult=1)`,
with a polling interval of at least 1.5 seconds and the same-server/IP requirement.

When `SLETAT_LOGIN` and `SLETAT_PASSWORD` are present, the production deploy smoke performs a read-only readiness check using `GetDepartCities` and `GetCountries`. It never prints credentials, query strings, or response bodies.

```env
SLETAT_LOGIN=
SLETAT_PASSWORD=
# VK_SLETAT_ENABLED=true
# SLETAT_BASE_URL=https://module.sletat.ru/Main.svc
```

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
