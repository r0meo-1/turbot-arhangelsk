# Travelpayouts booking and revenue analytics

TurBot uses Travelpayouts' current statistics API to enrich the Telegram admin
command `/partners [days]` with booking and earnings data.

## API

Endpoint:

`POST https://api.travelpayouts.com/statistics/v1/execute_query`

Authentication:

`X-Access-Token: <TRAVELPAYOUTS_API_TOKEN>`

The request asks for raw action rows inside the selected date window and
includes:

- `action_id`
- `sub_id`
- `price_eur`
- `paid_profit_eur`
- `state`
- `date`
- `updated_at`
- `created_at`

The query uses `type = action` and a mandatory date range. Responses are
paginated up to the API's 10,000-row page limit.

## TurBot SubIDs

The reporting client recognizes both current and historical identifiers:

| SubID | Service |
| --- | --- |
| `tg_hotels` | Yandex Travel hotels |
| `tg_esim` | Airalo eSIM |
| `turbot_esim_tg` | historical Airalo Telegram placement |
| `tg_transfer` | current Kiwitaxi transfer placement |
| `tg_kiwitaxi_transfer` | historical Kiwitaxi transfer placement |

Rows from unrelated Travelpayouts tools are ignored.

## Metrics shown in `/partners`

For the requested 1–365 day period TurBot reports:

- total tracked Travelpayouts bookings/actions;
- paid bookings;
- processing bookings;
- canceled bookings;
- confirmed affiliate earnings (`paid_profit_eur`);
- value of non-canceled bookings (`price_eur`);
- breakdown by hotel / eSIM / transfer;
- aggregate active-bookings-to-affiliate-clicks ratio.

The last ratio is **not user-level attribution**. Local click events deliberately
do not contain Telegram IDs, and Travelpayouts action rows are not joined to a
`chat_id`.

## Reliability

- Missing token: local click analytics still work; revenue is shown as not configured.
- Travelpayouts outage/error: local click analytics still work; the command reports
  that external statistics are temporarily unavailable.
- Duplicate action updates: the newest record per `action_id` wins.
- API result cache: `TRAVELPAYOUTS_STATS_CACHE_TTL=300` by default.
- `/partners reload` or `/partners 90 reload` bypasses that cache once.
- `/health` exposes only safe operational metadata: configured/not configured,
  cache windows, last success/error age, and a sanitized error code. It never
  returns the API token.
- No Travelpayouts API response is persisted into SQLite.

## Production environment

```env
TRAVELPAYOUTS_API_TOKEN="..."
TRAVELPAYOUTS_MARKER=778488
TRAVELPAYOUTS_TRS=574782
TRAVELPAYOUTS_STATS_CACHE_TTL=300
```

Keep the API token only in the server environment. Marker and project ID are
not secrets but are also kept configurable for staging/production separation.
