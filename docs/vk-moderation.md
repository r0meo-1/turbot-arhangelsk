# VK Mini App: preparation for direct-link moderation

Target: app 54475121, community 240310110. This checklist targets basic approval for distribution by link, not catalog inclusion. Readiness is not moderation approval.

## Release gate

- Match the deployed revision to the reviewed PR and public `/vk/health`.
- Check public entry, assets, legal pages and `legal.json`; unsigned writes must fail authentication.
- Check that privacy/consent identify the canonical operator even without JavaScript or when legal configuration is offline/invalid.
- Run VK form/Bridge/server browser tests, signed-owner/duplicate/cancel tests, privacy erasure, and result/favorite persistence tests. Use isolated databases and stub delivery for the full-erasure and final-enquiry paths.
- Real Windows acceptance: own synthetic form -> review -> save -> confirmed message in own chat -> separate search -> Back -> Cancel. Never confirm manager delivery or create a booking/payment.
- Compare lead/CRM counts before/after; confirm own draft, favorites and result snapshot cleanup. Preserve historical leads and other users' data. Do not run full erasure against the operator's production account.

## Security and data

- All draft/continue/partner-link writes authenticate signed launch parameters before applying a persistent per-owner budget of 30 requests per minute. Counters are shared across workers; HTTP 429 supplies Retry-After. Cancellation is excluded so a user can always cancel.
- Rate counters contain only owner ID, minute and count, expire during subsequent writes and are removed by full user erasure. This application budget does not replace infrastructure DDoS protection.
- Existing signature expiry/app/community checks, body cap, data minimization, explicit terms/consent and idempotent delivery remain required. No signed launch data, tokens or user contacts in QA output.

## Cabinet verification before submission

Check the existing application/ticket first. Record actual status and feedback; missing email is not proof of a pending or approved request. Verify description, official community, launch URLs for each selected platform, privacy/terms links, icons/screenshots and current required fields. Use the cabinet's current asset dimensions, not guessed sizes. Do not create another app or duplicate a pending application.

The moderator description must disclose that form parameters are saved in Mini App and selection continues in the community bot. Search and delivery to a manager are separate actions. Prices are preliminary; no booking or payment occurs in this flow.

## Evidence and limits

Keep a dated readiness report, native Windows QA and a submission-text draft under the task outputs. Real Android/iOS testing remains unconfirmed unless performed on actual supported devices; browser widths with mocked Bridge are not mobile-device acceptance.

Current official rule text and the private moderation cabinet are separate evidence items. Do not label an agency checklist as the complete current VK rules. No catalog-only additions or monetization changes are required by this preparation task.

Research (supporting material, not a replacement for current VK rules):
- VK team: https://habr.com/ru/companies/vk/articles/961286/
- Developer's direct-link vs catalog experience, 31 August 2026: https://kupiapp.ru/posts/2026-08-31-opyt-prokhozhdeniya-moderatsii-vk-mini-apps
- Vendor's own configuration/moderation procedure: https://www.5systems.ru/help/sozdanie-prilozheniya-kompanii-v-vk
- Agency's implementation experience: https://habr.com/ru/companies/doubletapp/news/1009470/
