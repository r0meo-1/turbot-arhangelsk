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

## Canonical VK links

Use the same campaign tags when traffic is sent to VK:

- Landing: `https://vk.me/club240310110?ref=landing`
- Pain creative: `https://vk.me/club240310110?ref=video_pain`
- Dream creative: `https://vk.me/club240310110?ref=video_dream`
- Thailand vs Vietnam: `https://vk.me/club240310110?ref=video_vs`

VK campaign attribution is read from `message_new.object.message.ref`. It is stored
as `source_tag`, separately from the signed Mini App launch fields `vk_ref` and
`vk_platform`.

## Attribution rules

- Source tags are restricted to ASCII letters, digits, underscore, and hyphen, 1-64 characters.
- Tags are canonicalized to lower case.
- Attribution is **first-touch for an active lead**. A later deep link or direct Mini App launch must not replace an already valid source tag.
- After a lead is completed and its active session is deleted, a later new lead may acquire a new source.
- Telegram Mini App attribution is accepted only from Telegram-signed `initData.start_param`, not arbitrary browser JSON.
- VK Mini App launch context keeps its signed `vk_ref`/`vk_platform`; the campaign `source_tag` survives the handoff from the chat referral into the Mini App draft.
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

## VK production smoke

1. Open a fresh VK conversation with one canonical `?ref=<tag>` link.
2. Send the first message or press the start button and enter the trip flow.
3. Confirm the active session has the expected first-touch source in admin diagnostics/export.
4. Open the VK Mini App, save a trip draft, return to chat, review it, and complete the lead.
5. Confirm the completed lead and manager notification contain the same campaign source.
6. During an active lead, reopen VK through a different `?ref=<other_tag>` link. Confirm the original source is retained.
7. After the first lead is complete, start a genuinely new lead from another campaign and confirm the new source can be acquired.
8. Keep `vk_ref` and `vk_platform` visible separately when debugging Mini App launch context; they are not campaign tags.

Record the live VK evidence in #113. Automated tests prove the data path, not that VK delivered a real referral event in production.


## Redacted evidence verifier

After the real lead is completed, copy the manager notification into a local
UTF-8 text file (for example `manager.txt`) and the relevant admin `/export`
output into another file (for example `export.txt`).

Run:

```bash
python scripts/verify_campaign_evidence.py \
  --source-tag video_pain \
  --manager-file manager.txt \
  --export-file export.txt
```

A passing result looks like:

```text
campaign_evidence_status=PASS
source_tag=video_pain
manager_marker=present
export_marker=present
manager_sha256=<sha256>
export_sha256=<sha256>
```

The verifier never echoes the copied manager/export contents, so its stdout can
be attached to #113 without exposing the customer's name, phone number or
Telegram/VK identifier. Keep the original text files private; the hashes identify
the exact evidence snapshots used by the check.

The verifier proves that the same campaign tag is present in both the manager
handoff and durable admin export. It does **not** replace the requirement that the
lead itself entered through a real Telegram/VK campaign link in production.
