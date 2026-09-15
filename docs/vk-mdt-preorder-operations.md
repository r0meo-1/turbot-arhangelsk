# VK MDT preorder operations

Production VK currently uses `VK_MDT_MODE=preorder`. In this mode TurBot creates a temporary tourist in MDT and then creates the preorder.

## Why preorder is not retried automatically

A preorder write is a multi-step CRM transaction. If the tourist was created but the response was lost before the preorder result was persisted, blindly replaying the whole transaction can create duplicate CRM entities. For that reason VK persists the one-shot outcome instead of treating preorder like the idempotent lead retry queue.

A successful preorder is stored as `mdt_status=synced` together with the non-sensitive MDT preorder/tourist IDs. A failed preorder is stored as `mdt_status=failed`.

## Health telemetry

`/vk/health` exposes aggregate delivery state under `mdt_delivery`:

- `mode`: current VK MDT mode.
- `total`: number of locally stored VK leads.
- `synced`: completed MDT writes.
- `failed`: one-shot writes that failed and require operator attention.
- `pending`: lead-mode retry queue entries, when lead mode is used.
- `unset`: leads that did not use MDT delivery.
- `latest_failed_seconds`: age of the most recent failed MDT write.

The health response contains no phone numbers, names, usernames, VK IDs, chat IDs, or stored lead payloads.

## Failure alert

When a preorder write fails, VK sends a rate-limited operations alert through the existing Telegram admin channel if `ADMIN_ERROR_ALERTS=true` and `BOT_TOKEN` plus admin recipients are configured.

The alert contains only aggregate counts and the current MDT mode. Repeated failures share the stable key `vk_mdt_preorder_failed`, so `ERROR_ALERT_COOLDOWN` prevents notification storms.

The alert is deliberately not an automatic retry. The operator can inspect MDT and the local delivery state before deciding whether a manual replay is safe.
