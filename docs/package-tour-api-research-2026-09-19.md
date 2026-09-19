# Package-tour API research — 2026-09-19

This note records current public capabilities and the integration strategy for TurBot.
Do not put credentials in this file.

## Executive conclusion

TurBot should keep package-tour search behind the existing provider router and preserve the rule:

> persist the lead first; external search is enrichment and must never block delivery to Natalya.

Recommended production order after credentials are issued:

```
TOUR_PROVIDER_ORDER=sletat,tourvisor,travelata
```

Level.Travel is a useful additional fallback/partner candidate after API approval.
Qui-Quo should remain a manager/CRM workflow unless its support team provides an official API/webhook contract.

## 1. Tourvisor Search API

Official docs:
- https://api.tourvisor.ru/search/docs
- https://tourvisor.ru/b2b/ddapi
- https://tourvisor.ru/b2b/tariffs

Current facts:
- JSON API, base path `/search/api/v1`.
- Bearer JWT authentication.
- Search is asynchronous: start search -> poll status -> get accumulated results.
- Search supports `onlyDirect`.
- Separate endpoint exists for tour flights and current price: `/tours/{tourId}/flights`.
- Trial mode: 300 total API requests/day.
- Runtime limits: reference methods 120 requests/min; search-related methods 300 requests/min.
- Search API public price starts from 5,000 RUB/month.
- Hotel descriptions are a separately priced API product.

TurBot status:
- search adapter exists;
- direct-flight flag is now forwarded to both the country directory and tour search;
- JWT still pending activation/issuance.

Next engineering step after JWT:
- live smoke against departures/countries;
- one bounded search;
- verify `/tours/{tourId}/flights` response before using an offer as “actual”.

## 2. Sletat.ru gateway

Official/current docs:
- https://sletat.ru/agency/xml/
- https://wiki.sletat.ru/w/Шлюз_поиска_туров_(json)

Current facts:
- Gateway is asynchronous.
- Correct flow: `GetTours` -> poll `GetLoadState` -> `GetTours(requestId, updateResult=1)`.
- Their docs recommend a minimum polling pause of 1.5 seconds.
- Search results should be saved locally; gateway results are not retained indefinitely.
- `ActualizePrice` checks current availability, hotel/flight state and mandatory surcharges.
- `SaveTourOrder` can create an order in Sletat and trigger manager notifications.
- Public pricing for “Search tours” gateway: 20,000 searches/month; 15,000 RUB/3 months, 24,000/6 months, 42,000/12 months.
- Sletat advertises more than 130 tour operators; actual operator access may depend on the licence.

TurBot status:
- correct asynchronous search/poll flow already exists;
- 1.5-second minimum poll interval is enforced;
- direct-only filtering already exists;
- `ActualizePrice` is not implemented yet;
- test licence/login/password are pending.

Next engineering step after credentials:
- live reference smoke;
- live search smoke;
- implement and test `ActualizePrice` for a selected offer before any “confirmed/current price” wording;
- keep `SaveTourOrder` optional because the canonical lead owner remains Natalya/agency CRM.

## 3. Travelata Partner API

Current documentation:
- https://support.travelpayouts.com/hc/ru/articles/360022674591-API-от-Travelata
- https://traff.travelata.ru/

Current facts:
- Base URL: `https://api-gateway.travelata.ru`.
- API requests use `/partners`.
- JSON/UTF-8.
- HTTP Basic Auth.
- Login/password are issued individually.
- Partner programme contact published by Travelata: `af@travelata.ru`.

TurBot status:
- server-side client already exists;
- credentials requested 2026-09-19;
- no credential found in Gmail before the request.

Role:
- fallback package-tour source after Sletat/Tourvisor;
- do not expose credentials or partner URLs in client-side code.

## 4. Level.Travel

Current documentation:
- https://help-partners.level.travel/api
- https://help-partners.level.travel/about
- https://support.travelpayouts.com/hc/ru/articles/360019529079-API-от-Level-travel

Current facts:
- Partner API supports tour search and price/availability actualization.
- Level explicitly lists chatbots, mobile apps and custom search engines as API use cases.
- Access is individual; approved partners receive an API key.
- Commercial model may be revenue share if Level handles sale, or a fixed search-based fee when the partner sells independently.
- API access contact: `partners@level.travel`.

TurBot status:
- access requested 2026-09-19;
- do not implement against undocumented/old endpoints before current docs/key are supplied.

Role:
- strong fallback candidate;
- useful for inventory diversity;
- booking handoff must not silently bypass the agency manager.

## 5. Qui-Quo

Current public page:
- https://qui-quo.ru/

Current facts:
- browser/manager tooling;
- CRM integrations with U-ON.Travel, MAG.Travel, amoCRM, TourControl and Sletat TourOffice are advertised;
- custom CRM integration is explicitly offered;
- no public technical API/webhook contract was found in current public documentation.

TurBot status:
- integration/API documentation requested 2026-09-19.

Role:
- manager handoff, selections and operator-cabinet workflow;
- not a package-tour search provider unless Qui-Quo supplies an official search contract.

## 6. Data integrity rules for production

1. Search price is not a guaranteed final price.
2. Before manager confirmation, actualize the selected offer using the provider's supported method.
3. Preserve provider name, search/request id and tour/offer id with the lead.
4. Never merge prices from different providers into one synthetic offer.
5. Never advertise “direct” unless the upstream provider filter/flight data confirms it.
6. Never block lead persistence/delivery because an external provider is down.
7. Store all provider credentials only in production secrets/environment.
