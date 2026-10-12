# Alean search integration

The VK tour funnel can search Alean through `shared/tour_providers.py`.
The main Python Telegram bot (`bot.py`) also searches Alean after saving and
notifying the manager about a completed request. It sends up to three indicative
offers to the customer and a linked follow-up to the manager. Empty or failed
searches continue through the existing Tutu/AI fallback. Supplier text is sent
as plain text; the transfer exclusion and manager-check notice are always retained.
`shared/alean.py` implements the read-only SAPI 2.3 contract supplied by Alean
on 7 August 2026 in `AleanSearchAPI.DOCX`. It uses HTTPS Basic authentication
and the documented XML response format at
`https://sapi.alean.ru:3443/services`.

## Activation

Keep credentials in the server environment, never in this repository or a
browser. Both explicit activation and routing are required:

```dotenv
ALEAN_ENABLED=true
ALEAN_USERNAME=<dedicated SAPI user>
ALEAN_PASSWORD=<secret>
TOUR_PROVIDER_ORDER=alean,travelata,tourvisor
```

Restart the Telegram and/or VK service after configuration. VK's tour-search health section
reports configured/enabled flags without exposing credentials. Sletat retains
its existing automatic priority when enabled.

Alean's 5 October 2026 account reply says that a new user can be added to the
existing agency account. Provisioning requires an agency letterhead request
signed by the responsible person, with agency tax ID and user details. This
adapter does not provision or activate supplier accounts. Alean's 28 September
reply says SAPI has no separate test endpoint; do not treat SOAP test credentials
as verified SAPI access. On 12 October, the dedicated account passed authenticated
`GetCountries` (HTTP 200, ten countries) and the other four catalog checks.
One synthetic Moscow–Sochi search for 11 November 2026, seven nights, two adults
and a seven-year-old child returned HTTP 200 with zero accepted offers. Its first
attempt exceeded the default ten-second timeout; a diagnostic repeat with a
45-second timeout completed in 6.51 seconds. This establishes SAPI access and a
valid search response, not available inventory or SOAP actualization. Customer
traffic remains disabled.

Further authenticated diagnostics on 12 October found that the response contained
15 tour rows with fractional RUB prices, which the original integer parser rejected.
The adapter now retains kopecks, compares the original decimal against the budget,
and preserves fractional prices through JSON, Telegram/VK cards and the unconfirmed
actualization fallback. A separate country-level Russia search for the same dates,
nights and party returned 15 accepted offers, starting at RUB 76,228.11. These are
observed supplier search prices, not confirmed prices or bookings. Local rendering
checks used the captured offers without sending any messages.

The Sochi request returned district-level resort IDs (Adler, Lazarevskoye, central
Sochi and Mamayka) instead of the requested Sochi ID. The catalog supplies no parent
link. Exact resort filtering remains strict pending a verified supplier group mapping;
country-level search does not silently replace a customer's specific resort request.

## Search behavior

- Uses stable country, departure-city, resort and hotel CIDs from supplier
  catalogs. Catalogs are cached for 24 hours per account and worker, up to 32
  cache entries. Restarting a worker refreshes its cache.
- Requests hotel-plus-flight offers with available tickets and guaranteed
  hotel inventory. Country/resort/departure names must match uniquely.
- Passes actual child ages, including ages 12–17, to hotel search.
- Preserves total-budget versus per-person-budget semantics. No contact,
  customer name, phone number or messenger ID is sent to Alean.
- Rejects unsupported direct-flight conditions and date/night ranges wider
  than four values. SAPI would otherwise silently narrow those ranges.
- Filters malformed, stopped, hotel-only, out-of-range and over-budget rows.
  Alean outages fall through to the next configured provider.
- Catalog network requests do not hold the shared cache lock. A completed empty
  search carries a local completion marker so VK can offer an explicit retry
  above budget; an upstream failure does not carry that marker.
- Uses the existing VK selection and manager handoff. Alean cards explicitly
  say that transfers are excluded. The Python Telegram bot uses the same adapter,
  preserving trip-date versus departure-date semantics and original child ages.
  This does not add Alean to the standalone
  Telegram Serverless demo or create a new Mini App search endpoint.

## Price and availability confirmation

Search results are indicative, not a confirmed booking. Alean's 9 October
reply requires SOAP `Specify` methods for price actualization. SAPI `GetTours`
must not be substituted for that operation.

This change leaves Alean actualization explicitly `unknown` and
`confirmed=false`, with a manager-check instruction. Automated SOAP
actualization remains pending verified account access and an authenticated
contract test. The internal `alean:` card ID is not a supplier booking token;
the manager must locate the offer in Alean and recheck it before confirmation.
No booking, invoice, payment or customer-data API is called.

## Verification

```sh
python -m pytest tests/test_alean.py tests/test_tour_providers.py tests/test_tourvisor.py
python -m pytest tests/test_vk_provider_router_wiring.py tests/test_vk_live_tour_safety.py tests/test_vk_bot.py
python -m pytest tests/test_telegram_alean.py tests/test_bot.py
```

Fixtures follow the supplier's XML field names and use synthetic catalog IDs.
They are contract tests, not evidence that the agency account is activated.
After provisioning, first check `GetCountries` with credentials supplied through
the server environment, then run one synthetic search and verify manager handoff
before enabling customer traffic.

From the repository root, run the isolated read-only check with dedicated
credentials set securely as `ALEAN_USERNAME` and `ALEAN_PASSWORD`:

```sh
python -m scripts.alean_live_smoke
```

It checks authenticated `GetCountries` and one Moscow–Sochi query 30 days ahead,
seven nights, two adults and a seven-year-old child. Only its local adapter is
enabled; it does not start a bot, modify activation flags, send messages, create
leads or book anything. The JSON report includes operation/status counts and
sanitized errors, never credentials or upstream bodies. Exit 2 means missing
credentials; exit 1 means the authenticated catalog or search failed. Zero offers
with a successful `GetTours` response verifies access, not available inventory.

The Telegram Tutu fallback now uses separately selected nights to derive the
return date when the customer supplies a departure date or preset. Explicit trip
ranges keep their stated return date.
