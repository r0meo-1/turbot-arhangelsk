# Campaign attribution production smoke

Tracking issue: #113

## Canonical Telegram links

Bot deep links:

- Landing: `https://t.me/apreltour_bot?start=landing`
- Pain creative: `https://t.me/apreltour_bot?start=video_pain`
- Dream creative: `https://t.me/apreltour_bot?start=video_dream`
- Thailand vs Vietnam: `https://t.me/apreltour_bot?start=video_vs`

If **Main Mini App** is configured for `@apreltour_bot` in BotFather, Telegram supports
opening it directly without a short name:

- `https://t.me/apreltour_bot?startapp=video_pain`
- `https://t.me/apreltour_bot?startapp=video_dream`
- `https://t.me/apreltour_bot?startapp=video_vs`

A named Direct Mini App instead uses:

`https://t.me/apreltour_bot/<short_name>?startapp=<tag>`

Do not invent a short name. Verify the BotFather configuration first.

## Attribution rules

- Source tags are restricted to ASCII letters, digits, underscore, and hyphen, 1-64 characters.
- Tags are canonicalized to lower case.
- Attribution is **first-touch for an active lead**. A later deep link or direct Mini App launch must not replace an already valid source tag.
- After a lead is completed and its active session is deleted, a later new lead may acquire a new source.
- Mini App attribution is accepted only from Telegram-signed `initData.start_param`, not arbitrary browser JSON.
- Completed leads persist `source_tag`; manager notifications and admin export expose it for QA.

## Telegram production smoke

1. Open a fresh chat/session using one canonical `?start=<tag>` link.
2. Complete consent and enter the trip flow.
3. Open the Mini App from TurBot, fill the form, review, and save it.
4. Choose a contact method, review the lead, and send it to the manager.
5. Confirm the manager notification contains the expected `Источник` value.
6. Run admin export and confirm the lead contains `src=<tag>`.
7. Before completing another fresh test, reopen the active lead through a different `?start=<other_tag>` link. Confirm the original source is retained.
8. Complete the lead, then start a new lead using a different tag. Confirm the new lead may use that new source.
9. If Main Mini App is configured in BotFather, repeat the flow from `?startapp=<tag>` and confirm the same source reaches the completed lead.

Record screenshots or message IDs in #113. Do not mark attribution production-ready from unit tests alone.

## Remaining acceptance item

VK campaign attribution must be verified separately for campaign paths that send traffic to VK. Do not infer Telegram attribution support means VK parity.
