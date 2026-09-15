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
  "budgetScope": "per_person",
  "directOnly": false,
  "consent": true,
  "source": "telegram_mini_app"
}
```

`budgetScope` is either `per_person` or `total`. For backwards compatibility, Telegram payloads without the field are treated as `per_person`, while legacy VK Mini App payloads without it are treated as `total` by the VK adapter.

`source` is advisory on the browser payload. The backend assigns the authenticated platform source (`telegram_mini_app` or `vk_mini_app`) before persisting the draft.
