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

The Sochi request returned district-level resort IDs instead of the requested Sochi
ID. The SAPI catalog supplies no parent link. Alean's official hotel hierarchy and
authenticated catalog names now establish this limited membership:

| SAPI resort name | Official child of Sochi city-resort |
| --- | --- |
| Адлер | https://www.alean.ru/hotels/rossiya/krasnodarskiy_kray/sochi_gorod-kurort/adler/ |
| Лазаревское | https://www.alean.ru/hotels/rossiya/krasnodarskiy_kray/sochi_gorod-kurort/lazarevskoe/ |
| Мамайка | https://www.alean.ru/hotels/rossiya/krasnodarskiy_kray/sochi_gorod-kurort/mamayka/ |
| Сочи центр | https://www.alean.ru/hotels/rossiya/krasnodarskiy_kray/sochi_gorod-kurort/sochi_tsentralnyy_rayon/ |

Only a Russia/Sochi request accepts those uniquely named catalog members, with
their current IDs resolved from SAPI. The outbound request still uses Sochi's CID.
An explicit Adler request remains Adler-only. Unverified districts, ambiguous
names, unknown IDs and other countries are not accepted. Country-level search
does not replace a specific resort request.

The corrected Moscow–Sochi synthetic query for 11 November 2026, seven nights,
two adults and a seven-year-old child returned 15 accepted offers in 19.05 seconds
for the entire catalog/search check, starting at RUB 24,830.72 (Колибри,
Лазаревское). It used a 45-second diagnostic timeout; application timeout defaults
were unchanged. SOAP actualization and customer traffic remain disabled.

The application now gives `GetTours` a 30-second read timeout while keeping its
connection timeout and catalog timeouts at ten seconds. A country-level search
had taken 14.22 seconds even with cached catalogs, exceeding the old limit.
`ALEAN_SEARCH_TIMEOUT_SECONDS` optionally sets the search read timeout from 10 to
60 seconds; missing, malformed, nonfinite or out-of-range values use 30 seconds.
Requests timeouts bound connection/read inactivity, not the entire funnel or
the cumulative time for all catalogs. A timeout still falls through to the next
provider and does not mark a search as completed.

The read-only smoke check on 12 October then passed using these application
defaults, without a diagnostic timeout override: all five catalogs and `GetTours`
returned HTTP 200, with 15 accepted Sochi offers in 5.74 seconds overall.
The isolated adapter was enabled only inside the check; application activation
remained off. This validates the search path, not SOAP confirmation or delivery
to Telegram/VK users.

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
- VK retains the search generation through completion and checks it after
  waiting-message edits and before result publication. An old worker cannot
  replace a newer selection or send its empty-search hint after those checks
  detect a new search or parameter edit. Editing the hotel or navigating back
  from review also clears the previous search and selection. The wait animation stops when search ends and
  restores final text only for its own current review.
- VK selection buttons bind both the search generation and the displayed offer,
  including its price. A stale button cannot select a different hotel after a
  new search, filtering or sorting, even when supplier IDs are absent or shared.
  Pagination keeps the global offer numbers; a manually typed number selects
  from the current list.

## Price and availability confirmation

Search results are indicative, not a confirmed booking. Alean requires SOAP
`Specify` methods for price actualization. SAPI `GetTours`
must not be substituted for that operation.

The [official API comparison](https://www.alean.ru/page/ta-api/?label=diff)
distinguishes the full SOAP service from SAPI and says its documentation is
provided on application. The documentation, public HTTPS WSDLs and request/result
schemas have been read. The contract uses SOAP 1.1 RPC/encoded;
`SpecifyTourPackageReservation` takes `SessionID`, `ReservationDataXML` and
`OverrideBaggagePlaces`. Supplier package/transport lines are required, and
`Confirm=true` means confirmation is required, not that inventory is confirmed.
One HTTPS test Login returned `lrAccessDenied`, so authenticated SOAP package
search and Specify remain unperformed. Matching public WSDLs do not establish
that the HTTPS route uses the test database. Successful SAPI access is not
evidence of SOAP authorization; credentials are never sent over HTTP.

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
leads or book anything. It uses the same timeout settings as the application.
The JSON report includes operation/status counts, configured timeouts, timings
to response headers (excluding subsequent streamed body reads), total elapsed time and
sanitized errors, never credentials or upstream bodies. Exit 2 means missing
credentials; exit 1 means the authenticated catalog or search failed. Zero offers
with a successful `GetTours` response verifies access, not available inventory.

The Telegram Tutu fallback now uses separately selected nights to derive the
return date when the customer supplies a departure date or preset. Explicit trip
ranges keep their stated return date.
