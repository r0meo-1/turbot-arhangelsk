# Biblio-Globus read-only export pilot

Issue #300. This is the transport stage of the package-tour provider, not a
finished customer-facing search integration. It is absent from provider order.

Official protocol: https://export.bgoperator.ru/load-xml-prices.html
Authentication clarification: https://export.bgoperator.ru/api-prices.html

`shared.biblio_globus.ExportClient` supports only countries, resorts, a single
hotel dictionary entry, price-list discovery and a dated/nights-filtered price
export. Credentials are read from server-side `BIBLIO_EXPORT_LOGIN` and
`BIBLIO_EXPORT_PASSWORD`; `BIBLIO_EXPORT_ENABLED` defaults to false. Credentials
are excluded from settings repr. Production environment files are not changed.

Use a context manager to close its dedicated Session. Requests require HTTPS,
gzip, fixed vendor hosts, disabled redirects, timeouts and bounded decompressed
response size. One 401 may trigger form-body authentication and one replay;
rotating session cookies are serialized. Diagnostics omit bodies, URLs and
credentials. Never reuse the vendor session for another provider.

The export response is raw provider data. `minprice` is documented as per-person
for a two-adult placement; it must not become TurBot's whole-trip price. Confirm
the exact placement-level `prices.amount` / `RUR` scope before normalization.
The dictionary `raiting` is popularity, not a customer review score.

Remaining before the provider acceptance can be completed:

- Confirm group-price semantics and placement age matching.
- Normalize currency/total price without using list minimums or invented facts.
- Apply stop-sale/quota checks and refuse unsupported direct-flight searches.
- Route only after explicit opt-in and keep timeout/failure fallback.
- Authenticate one synthetic read-only smoke using agency-owned credentials.
- Resolve the non-PII final-price recheck with the provider. Tour API createtour
  and booking links remain outside this client's surface.
- Explicitly approve production enablement after the above evidence exists.

No authentication, passport transmission, booking, cancellation, payment,
partner-price mutation, or production enablement is performed by the tests.
