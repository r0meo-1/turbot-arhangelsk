# Provider access status — TurBot

Updated: 2026-09-19

This file records **configuration state only**. Never put real passwords, JWTs, API keys or licence credentials here.

## Tourvisor

Status: **PRO account exists; Search API access requested; JWT not yet issued/configured.**

Production model:
- Search API v1, server-side only.
- Authentication: Bearer JWT from the Tourvisor account after API activation.
- Runtime secret: `TOURVISOR_TOKEN`.
- Fast disable: `VK_TOURVISOR_ENABLED=false`.
- Manager fallback: `MANAGER_TOURVISOR_URL=https://pro.tourvisor.ru/`.

The bot must keep working when the token is absent or the API is unavailable.

## Sletat.ru

Status: **2-week test gateway requested on 2026-09-19; licence credentials pending.**

Production model:
- Server-side XML/JSON gateway.
- Licence may be bound to the production domain/IP.
- Runtime secrets: `SLETAT_LOGIN`, `SLETAT_PASSWORD`.
- Fast disable: `VK_SLETAT_ENABLED=false`.
- Default endpoint: `https://module.sletat.ru/Main.svc`.
- Manager fallback: `MANAGER_SLETAT_URL=https://sletat.ru/pro`.

Do not enable production search until Sletat confirms the licence binding and credentials.

## Qui-Quo

Status: **CRM/API integration request sent on 2026-09-19; no API credential found in Gmail.**

Role in TurBot:
- Manager/browser workflow.
- Quick login to tour-operator cabinets.
- Selections / CRM handoff if Qui-Quo provides a supported API or webhook.
- **Not** a package-tour search provider in `shared/tour_providers.py`.

Manager fallback: `MANAGER_QUIQUO_URL=https://qui-quo.ru/`.

If Qui-Quo provides API access later, implement it as a manager/CRM integration module, not as a fake search provider.

## Provider order

Recommended package-tour search order after credentials are available:

```
TOUR_PROVIDER_ORDER=sletat,tourvisor,travelata
```

Providers without valid credentials are skipped. Every accepted lead is persisted before provider search, so an external search outage must never lose a lead.

## Secret handling

Real credentials belong only in the production environment/secrets store:
- no Git commits;
- no `.env.example` values;
- no screenshots or chat messages;
- no client-side JavaScript.
