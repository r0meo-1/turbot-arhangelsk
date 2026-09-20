# TurBot travel CRM domain model

This module is based on observed real travel-agent workflow patterns from Natalia's handwritten working notebook. No real client phone numbers, e-mails, names or other notebook PII are used in code, fixtures or examples.

## Canonical workflow

`Customer → TripRequest/Lead → Quote[] → Activity/Message → Task → Booking/Outcome`

The current foundation implemented in `shared/travel_crm.py` covers the request, historical quotes, manager tasks and outcome primitive.

## Product rules

1. A request is not a single tour. It can contain several countries, hotels, date windows and budgets.
2. Every child has an individual age.
3. Attribution must survive the entire funnel: `source_tag`, channel, referrer and campaign.
4. Quotes are historical records. New calculations append; they do not overwrite older offers.
5. Manager work is explicit: selection, send options, call back, price check, flights, visa, documents and next contact.
6. Budget semantics matter: target, maximum ceiling or fixed amount.
7. Preferences can be operational, not just geographic: beach type, hotel territory, walkability, excursion proximity, meal requirements and special wishes.
8. Corporate/group travel is a request attribute.
9. Loss reasons such as `too_expensive` belong to outcome/contact history, not free-form folklore hidden in comments.

## Manager queue

`tasks_due_today()` returns overdue and due pending tasks in priority order. This is the backend primitive for the future “Что делать сегодня” manager screen.

## Privacy rule

Use synthetic data only in tests, demos, screenshots, seed data and GitHub. The notebook is product research, not a fixture source.

## Follow-up integration

- persist these entities in the current storage layer;
- map Telegram and VK qualification payloads into `TripRequest`;
- preserve `source_tag` into booking/outcome;
- render quote history in manager lead cards;
- expose manager task queue;
- add Activity/Message history as a separate append-only stream.
