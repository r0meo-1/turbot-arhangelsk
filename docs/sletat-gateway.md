# Sletat.ru search gateway — production integration notes

Source: vendor email received 2026-09-19 (forwarded from Sletat.ru, original dated 2026-09-18).

## What Sletat.ru offered

- Production **tour-search XML gateway** for websites/apps.
- Agent registration: https://sletat.ru/b2b/
- License is bound to a **domain name or IP address** supplied to Sletat.ru.
- Free test period: **2 weeks**.
- Technical support: help@sletat.ru.
- Product page: https://pro.sletat.ru/xml-gates/
- Documentation: https://wiki.sletat.ru/w/Категория:Шлюзы_поиска_туров

## Vendor pricing quoted in the email

Basic tour-search gateway:
- 3 months: 15,000 RUB
- 6 months: 24,000 RUB
- 12 months: 42,000 RUB
- 20,000 search requests/month included
- excess: 0.10 RUB/request

Extended bundle:
- 3 months: 90,000 RUB (quoted instead of 105,000)
- 6 months: 165,000 RUB (quoted instead of 180,000)
- 12 months: 288,000 RUB (quoted instead of 360,000)
- 30,000 search requests/month included

Extended capabilities described by the vendor:
- tour operator data + operator-system link;
- detailed actualization: surcharges, departure time and airport;
- hotel database: structured descriptions, services, map, photos, contacts;
- hotel reviews and ratings;
- hot-tours gateway.

## TurBot integration decision

Use the Sletat gateway as a **server-side provider**, not browser automation.

Flow:
1. User submits Telegram / VK / website lead.
2. Lead is durably saved first.
3. Lead is delivered to Natalya Ilyina in VK PM (VK id 112655584).
4. Search provider router may query Sletat and/or Tourvisor after persistence.
5. Results are manager assistance only until commercial facts are confirmed.
6. Natalya can continue in Tourvisor PRO, Sletat PRO or Qui-Quo from the manager handoff.

## Required configuration

Do not commit credentials.

```dotenv
SLETAT_ENABLED=false
SLETAT_BASE_URL=
SLETAT_LOGIN=
SLETAT_PASSWORD=
SLETAT_LICENSE_HOST=
SLETAT_TIMEOUT_SECONDS=20
SLETAT_MAX_OFFERS=15
```

The exact authentication and request contract must be implemented only from the active vendor documentation / credentials supplied after registration. Do not guess private endpoint parameters.

## Launch checklist

- [ ] Register Aprel Tour as an agency at sletat.ru/b2b.
- [ ] Give Sletat.ru the production domain or fixed server IP for the license.
- [ ] Activate the 2-week test.
- [ ] Obtain credentials and confirm allowed request rate.
- [ ] Implement a provider adapter behind the existing provider router.
- [ ] Add deterministic normalization and no-fabrication tests.
- [ ] Add timeout / retry / quota observability.
- [ ] Verify one production search from a real lead.
- [ ] Verify failure of Sletat never prevents the lead reaching Natalya.
- [ ] Review whether hotel descriptions/photos/reviews are licensed for the exact display surfaces before publishing them to end users.
