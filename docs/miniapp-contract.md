# TurBot Mini App trip-request contract

Telegram and VK Mini Apps use the same `trip_request` payload shape. Platform-specific authentication stays separate, while trip semantics stay explicit.

```json
{
  "type": "trip_request",
  "version": 2,
  "destination": "Таиланд",
  "departure": "Архангельск",
  "date": "2027-01-15",
  "nights": 10,
  "adults": 2,
  "children": 0,
  "childrenAges": [],
  "budgetMaxRub": 270000,
  "budgetScope": "total",
  "directOnly": false,
  "consent": true,
  "source": "telegram_mini_app"
}
```

Current Telegram and VK Mini Apps send `budgetScope: "total"`, so `budgetMaxRub` means the maximum budget for the whole trip. The backend still accepts `per_person` for compatibility with older clients and API callers. Telegram payloads that omit `budgetScope` retain the legacy `per_person` default, while legacy VK Mini App payloads without it are treated as `total` by the VK adapter.

`source` is advisory on the browser payload. The backend assigns the authenticated platform source (`telegram_mini_app` or `vk_mini_app`) before persisting the draft.
