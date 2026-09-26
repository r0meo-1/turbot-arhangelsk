# VK marketing autopost

This is the organic VK publishing lane for the TurBot / «Апрель Тур» winter
campaign. It deliberately reuses the existing VK community token and the same
campaign-attribution contract already used by the bot.

## Marketing logic

The recurring content mix tests three different customer motivations without
changing the product promise:

- **Pain** — reduce the friction of searching many sites.
- **Dream** — lead with the desired winter-beach outcome.
- **Battle** — help the customer structure a Thailand-vs-Vietnam comparison.

Every post has its own referral tag:

- `vk_post_pain`
- `vk_post_dream`
- `vk_post_battle`

The CTA points to `https://vk.me/club240310110?ref=<source_tag>`. The existing
VK bot stores `message_new.object.message.ref` as `source_tag`, so the same tag
can survive through lead creation and manager handoff.

## Schedule

The plan lives in `marketing/vk_autopost_plan.json` and uses
`Europe/Moscow`:

| Angle | Slot |
| --- | --- |
| Pain | Tuesday 19:30 |
| Dream | Thursday 12:30 |
| Battle | Sunday 18:30 |

The organic schedule was activated on **2026-09-26** after the attribution gate
had passed. The first active slot is **Sunday, 2026-09-27 at 18:30
Europe/Moscow**.

The repository plan remains the emergency kill switch:

1. `marketing/vk_autopost_plan.json` must have `"enabled": true`.
2. The scheduled workflow passes `VK_AUTOPOST_ENABLED=true` explicitly.

To pause scheduled publishing, set the plan back to `"enabled": false`.
Manual publication still requires the explicit `--force` path and is separate
from the recurring schedule.

## Secrets and variables

The scheduled workflow reuses the existing settings used by VK tooling:

- GitHub secret `VK_TOKEN`
- GitHub variable `VK_OWNER_ID`

No token is written to the repository or logs.

## Manual QA

Preview copy and links without any VK write:

```bash
python vk_autopost.py preview
python vk_autopost.py preview --slug battle
```

Manual publication requires an explicit write flag:

```bash
VK_TOKEN=... VK_OWNER_ID=-240310110 \
  python vk_autopost.py post --slug pain --force
```

## Duplicate protection

Two layers are used:

1. a local SQLite ledger keyed by `campaign + slug + ISO week`;
2. a `wall.get` check for the same `ref=<source_tag>` marker on the VK wall
   during the current ISO week.

The second layer is the durable one for GitHub Actions, whose runners are
ephemeral. A retried scheduled job therefore does not become a duplicate post.

## Visuals

The post plan supports an optional VK `attachments` value. Keep it blank until
the final Pain / Dream / Battle assets have stable VK attachment identifiers.
Do not put expiring Canva/AI preview URLs in the production plan.

## Success measurement

Treat this lane as acquisition, not vanity posting. Compare each
`vk_post_*` tag through the existing funnel and manager handoff. Useful
campaign metrics are CTR/click starts, start-to-lead conversion, and lead volume;
reactions alone are not the optimization target.
