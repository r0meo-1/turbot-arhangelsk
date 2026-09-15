# Telegram MDT retry operations

Telegram MDT retries are active only when `MDT_ENABLED=true`, `MDT_MODE=lead`, `MDT_RETRY_ENABLED=true`, and demo mode is off.

Failed lead writes remain in SQLite with `mdt_status=pending` and are retried with bounded exponential backoff. A successful write becomes `mdt_status=synced`. Every attempt for the same local lead keeps the stable delivery key `tg-lead-<local lead id>`.

## Health telemetry

`/health` exposes aggregate retry telemetry under `mdt_retry`:

- `pending`: queued leads waiting for a successful MDT write.
- `due_now`: queued leads whose retry time has arrived.
- `max_attempts`: largest attempt count in the pending queue.
- `oldest_pending_seconds`: age of the oldest queued lead.
- `next_retry_in_seconds`: time until the next scheduled retry.

These fields are aggregate only. The health response does not expose phone numbers, names, usernames, chat IDs, or stored lead payloads.

## Stale queue alert

`MDT_RETRY_ALERT_AFTER_SECONDS` controls when the existing rate-limited Telegram admin alert channel reports a persistently stale queue. The default is `7200` seconds (2 hours). Set it to `0` to disable only this backlog alert.

The alert includes only queue counts, maximum attempts, and oldest queue age. Dynamic counters use the stable alert key `mdt_retry_queue_stale`, so `ERROR_ALERT_COOLDOWN` still suppresses repeated notifications while the backlog remains stale.

Normal transient retry failures do not alert the admin.
