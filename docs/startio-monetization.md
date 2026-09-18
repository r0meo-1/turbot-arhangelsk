# Start.io monetization readiness

Status: **prepared, not enabled**.

TurBot currently ships as Python services plus Telegram/VK/web mini apps. Start.io's publisher SDK is designed for mobile apps (Android/iOS), so it must **not** be injected into the current web mini apps.

## When to enable Start.io

Enable only after TurBot has a native Android/iOS app or a native wrapper where the Start.io mobile SDK is officially supported.

## Production checklist

1. Create the app in the Start.io Publisher Portal and obtain the official App ID / ad-unit configuration.
2. Use the current Start.io SDK supported by the target platform. For Android, prefer the current 5.x line rather than legacy 4.x.
3. Prefer mediation/bidding if TurBot already uses a primary mediation layer.
4. Create separate placements for each context:
   - results_native
   - post_action_interstitial
   - rewarded_optional
   - return_ad
5. Do not show full-screen ads during onboarding, search form entry, checkout/lead submission, or while the user is completing a primary action.
6. Suggested starting caps:
   - interstitial: at most one per 4 minutes and only at natural transitions;
   - rewarded: user initiated only;
   - native: roughly one ad per 5-10 organic cards.
7. Preload full-screen inventory and never block the primary app action if an ad is unavailable.
8. Run test-mode coverage for cold start, warm start, background/foreground, offline/slow network, ad load failure, close/back navigation and repeated taps.
9. Configure consent/privacy before personalized ad requests where required.
10. Add the exact `app-ads.txt` entries from the Start.io Publisher Dashboard to the developer website.
11. Monitor revenue together with retention, lead conversion, crashes/ANRs and session quality.

## TurBot placement policy

The tour lead funnel is more valuable than an extra impression. Therefore:

- no Start.io ads in the initial questionnaire;
- no forced ad before search results;
- native ads may be tested inside long result feeds;
- interstitial may be tested only after a completed meaningful action;
- ads must never obscure operator/airline/hotel information or the CTA that creates a lead;
- ad failure must degrade to normal application behavior.

## Privacy

Do not add Start.io-specific disclosure to production privacy text until the SDK/data flow is actually enabled. Before release, update the privacy policy and consent flow using the then-current Start.io Publisher Agreement and End User Privacy Policy.

## app-ads.txt

Do not invent seller IDs. Copy the exact Start.io line(s) shown in the publisher dashboard and publish them on the same developer website used by the app-store listing.

A placeholder template is stored at `docs/app-ads.txt.example`.

## Official references

- https://www.start.io/mobile-monetization/
- https://www.start.io/glossary/monetization-sdk/
- https://www.start.io/policy/publisher-terms/
- https://www.start.io/policy/privacy-policy/
- https://www.start.io/blog/start-io-android-sdk-5-0-just-launched-heres-what-to-expect/
