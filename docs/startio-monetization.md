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

## Native safety infrastructure (no App ID required)

`AdPlacementPolicy` requires an explicit native-owned `AdFlow` and foreground state.
Unknown state, onboarding, search parameter entry, search loading, and lead/contact
submission prohibit every placement, including user-initiated rewarded ads.
Native inventory is eligible only when results are visible; interstitial inventory
only after a completed action. Rewarded inventory requires an explicit tap in a
safe flow. Return inventory remains prohibited. Legacy callers without flow state
fail closed.

`StartIoManager.mayRequest` additionally requires build configuration, successful
SDK initialization and consent submission, a stored consent decision, and the
existing persistent frequency gate. A denied personalization decision remains a
valid decision; TurBot always remains accessible. SDK initialization or consent
submission exceptions return failure without blocking TurBot.

`AdController` now separates eligibility from LOADING, READY, SHOWING, DISMISSED
and FAILED. It serializes requests/callbacks, uses generation tickets to reject
stale callbacks, and rechecks flow, foreground, current configuration, successfully
applied/stored consent and frequency immediately before display. Loading never
automatically displays an ad. A primary action must complete independently;
`showIfReady` returns immediately if inventory is unavailable. There are no retry
loops. Pending load/ready inventory expires after 30 seconds (checked on requests,
display attempts and load callbacks, with an explicit expiry hook for a future
timeout scheduler). Only a confirmed impression writes the persistent frequency timestamp;
clock rollback/overflow fails closed. The consent gate also rejects a stored
decision that differs from the decision successfully submitted to Start.io.

Navigation, back, backgrounding, destruction and privacy-choice changes invalidate
pending inventory. A showing ad retains its occupied slot until dismissal/failure
is confirmed, even if cancellation or show throws, preventing a second overlapping ad.
An uncertain thrown show call quarantines the occupied slot until the adapter confirms
a terminal event; it cannot be interpreted as proof that no SDK window opened.
A confirmed impression during cancellation still counts toward the cap. Rewards
require explicit request and display taps, an impression, and the adapter's verified
completion callback, once only; invalidation, close and failure cannot grant them.

The shell still has **no ad request/show call sites and no trusted WebView flow
bridge**. Its controller has an unavailable delivery adapter and permanent UNKNOWN
flow; even a configured build cannot load/show ads through this shell. The page-start
and navigation callbacks only invalidate; they never establish eligibility. No
JavaScript interface is installed. Resume does not restore inventory or safe state.

## Remaining integration and activation work for issue #98

Android-only inspection cannot establish authoritative funnel state in the remote
WebView. A separate coordinated frontend/native change must define explicit events
for onboarding, parameter entry, loading, results actually rendered, lead/contact
submission and completed actions. Specify and review the trust boundary before
implementation: exact approved HTTPS origins/main frame, authenticated session and
document identity, navigation generation, ordered events, and rejection of stale,
replayed, unexpected or subframe messages. Origin checks alone do not prove a
completed action. Do not accept arbitrary JavaScript claims or derive state from
URLs/page completion. Until the protocol and event producers are reviewed and tested,
keep UNKNOWN. SPA transitions must invalidate inventory before entering a protected
flow, including transitions without page navigation.

No real SDK delivery adapter is included. Future adapters must preserve the controller
contract: asynchronous/nonblocking load/show, bounded load timeout reported as failure,
inventory disposal, confirmed dismissal/failure after cancellation, correct SDK
impression event and verified rewarded completion event (before terminal dismissal).
Marshal SDK work to Android's UI thread and review SDK callback ordering/threading
against the pinned version. Do not wire ordinary display/close callbacks as reward
completion. Add controller call sites only after trustworthy flow integration, and
connect real gates to `StartIoManager.mayRequest` and impressions to
`AdFrequencyGate.markShown`. Define the optional reward product and durable redemption
semantics before exposing a rewarded CTA. Native card rendering, ad labeling, spacing
and disposal remain unimplemented. Return and Splash ads must remain disabled.

Publisher Portal registration, the real build-supplied App ID and dashboard placement
configuration are still required. No App ID or seller entries are included here.
Publish app-ads.txt only from exact dashboard entries. Review jurisdiction-specific
disclosures/CMP requirements and denied-personalization behavior before enabling any
SDK data flow; update production privacy text only with confirmed enabled behavior.
Keep production advertising disabled and issue #98 open throughout these steps.

Android CI runs policy/controller JUnit tests and builds the disabled debug APK
without an App ID. The controllable fake adapter tests protected/unknown flows,
missing configuration, granted/denied/unknown and changed consent, offline/load/show
failure, close/back, rapid/reentrant taps, background/resume, stale callbacks,
verified reward completion, and frequency boundaries. These are fake-ad infrastructure
tests, not live SDK, WebView integration, device validation or production consent
compliance. Real SDK callback ordering, network behavior, physical/emulated device
behavior, cancellation and reward delivery remain unverified.

Before activation, record device/build and outcomes for: cold/warm start; each
consent decision and later changes; all critical flows; offline/slow network;
load/show failure; close/back; rapid taps; background/resume; navigation after
preload; and the four-minute interstitial boundary. Each case must leave primary
actions usable and critical flows ad-free. Real inventory testing, publisher
registration, dashboard app-ads.txt lines, production disclosures, and revenue /
retention / lead-conversion / stability monitoring remain activation requirements.

## Privacy requirements

## Readiness and disablement checklist

| Layer | Current evidence | Required before activation |
| --- | --- | --- |
| Policy/controller | Four policy and thirteen fake-controller JUnit tests; disabled APK build in CI | Revalidate after real adapter and flow integration |
| Default shell | Build defaults are disabled/empty; no controller request/show call sites; UNKNOWN flow and unavailable adapter | Device/network inspection to confirm no ad delivery |
| Live SDK | Initialization and consent gates only | Real publisher App ID, SDK adapter/callback review and test inventory |
| Funnel integration | Navigation/lifecycle invalidation only | Reviewed trustworthy frontend/native protocol and protected-flow device tests |
| Production | Not enabled | Publisher configuration, disclosures/CMP review, exact app-ads.txt entries and monitoring |

Future test activation is a separate reviewed change, not a switch to turn on now:

1. Complete and test the real delivery adapter, trustworthy flow integration,
   native rendering and optional reward redemption contract. Keep defaults off.
2. Register the app and obtain the real ID; keep it in local/CI build configuration.
   Review consent/data-flow requirements before initializing on any test device.
3. Confirm the pinned SDK's supported test-inventory configuration from official
   documentation. Enabling the SDK alone does not establish test mode.
4. Build an isolated debug artifact with explicit enabled configuration and the
   real ID. Verify effective build configuration and run the device/network matrix
   above, including granted/denied consent and all protected flows.
5. Require a separate production release review before enabling delivery, with
   disclosures, publisher entries and retention/conversion/stability monitoring.

To disable in a subsequent build, explicitly set `STARTIO_ENABLED=false` and remove
`STARTIO_APP_ID` from local/CI build inputs (including user Gradle properties), then
rebuild and verify the generated configuration is disabled/empty. Never remove the
consent store to disable advertising. Existing installed enabled binaries do not
change when build inputs change: distribute the disabled replacement and verify it
on devices. No remote kill switch exists; do not claim immediate remote disablement.

The normal CI command runs `:app:testDebugUnitTest :app:assembleDebug` without ID or
enablement inputs. Source and fake-ad checks do not substitute for inspecting a
future enabled artifact or observing SDK network behavior on a device.

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
