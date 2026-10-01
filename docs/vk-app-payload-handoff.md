# VK Mini App app_payload handoff

Production handoff contract:

1. Mini App persists a signed VK draft first.
2. `VKWebAppSendPayload` may then send `{ "command": "miniapp_review", "version": 1 }`.
3. VK Callback API delivers `app_payload` to `/vk/webhook`.
4. The bot validates callback secret, app ID and community ID, restores the saved Mini App snapshot, and renders review.
5. This handoff does not create a lead. Lead creation remains behind the normal user confirmation flow.

Bridge success alone does not confirm bot message delivery. The saved snapshot
also resumes through the ordinary chat Start button or `Проверить заявку`, even
after a process restart or overwritten chat FSM state. `🧳 Новый подбор` explicitly
discards that snapshot and starts a fresh request; cancel/data deletion clear it
as before. Mini App keeps the recovery instruction visible after Bridge success.

The restricted production deploy entrypoint accepts `TURBOT_VK_CALLBACK_APP_PAYLOAD_V1` only to run the guarded callback-settings helper. The helper snapshots current callback event flags, enables only `app_payload`, verifies all other flags are unchanged, and attempts rollback if verification fails.
