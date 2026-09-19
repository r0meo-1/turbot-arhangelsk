# Campaign attribution smoke test

Issue: #113

## Canonical links

Use bounded tags containing only letters, digits, underscore, or hyphen.

- Landing: `https://t.me/apreltour_bot?start=landing`
- Pain creative: `https://t.me/apreltour_bot?start=video_pain`
- Dream creative: `https://t.me/apreltour_bot?start=video_dream`
- Thailand vs Vietnam creative: `https://t.me/apreltour_bot?start=video_vs`

These links open the bot and persist the first valid source tag through the active
session, completed lead row, manager notification, and admin export.

For a direct Telegram Mini App launch, use Telegram's `startapp` form only after
the actual BotFather Mini App short name is verified:

`https://t.me/apreltour_bot/<APP_SHORT_NAME>?startapp=<tag>`

Do not guess or hard-code `<APP_SHORT_NAME>`. The backend accepts Mini App
attribution only from Telegram's HMAC-validated `initData.start_param`.

## Manual production smoke

1. Open one canonical bot deep link in a fresh Telegram chat/session.
2. Complete consent/start and open the Mini App from TurBot.
3. Fill the trip form, review it, and save.
4. Choose a contact method, review the request, and send it to the manager.
5. Confirm the manager notification contains the expected `Источник` tag.
6. Run admin export and confirm the same `src=<tag>` is present.
7. Restart the flow with a different tag before completing it. Confirm the
   existing valid tag remains first-touch for that active session.
8. Complete a fresh second lead and confirm its attribution is independent.
9. When the BotFather Mini App short name is known, repeat steps 2-6 from the
   direct `startapp` URL and record the evidence in #113.

## Production-ready gate

Do not close #113 until CI is green and live Telegram evidence is recorded for
both the canonical bot deep link and the direct Mini App link. VK attribution
remains a separate acceptance item in #113.
