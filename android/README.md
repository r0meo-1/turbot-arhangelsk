# TurBot Android shell

Minimal native Android shell for the existing TurBot web experience.

## Status

The Android project contains a **gated Start.io SDK integration** plus a native privacy choice UI, but ads remain disabled by default.

Start.io can initialize only when all of the following are true:

1. the build is explicitly created with `STARTIO_ENABLED=true`;
2. a non-empty real `STARTIO_APP_ID` is supplied;
3. TurBot has a stored advertising-consent decision.

If any condition is missing, Start.io stays off.

## Current baseline

- package: `ru.r0meo1.turbot`
- target SDK: 36
- compile SDK: 36
- minimum SDK: 24
- Android Gradle Plugin: 9.4.0
- Gradle: 9.6.0
- Java: 17
- Start.io SDK: 5.3.1
- first-party entry point: `https://r0meo1.ru/apreltour/`
- cleartext HTTP: disabled
- file/content access from WebView: disabled
- non-first-party links: opened in the system browser
- Start.io automatic init provider: removed
- Start.io splash ads: disabled
- Start.io return ads: disabled

## Local setup

1. Install Android Studio with Android SDK 36 and JDK 17.
2. If wrapper scripts/JAR are not present, run:
   `gradle wrapper --gradle-version 9.6.0`
3. Open the `android/` directory in Android Studio.
4. Build/install the debug app.

The repository stores `gradle-wrapper.properties`, but not the generated wrapper JAR.

## Safe default build

This keeps Start.io disabled:

```bash
gradle -p android :app:assembleDebug
```

## Start.io-enabled build

After the app exists in Start.io Publisher Portal:

```bash
gradle -p android \
  -PSTARTIO_ENABLED=true \
  -PSTARTIO_APP_ID=<real-app-id> \
  :app:assembleDebug
```

The App ID is intentionally not committed. It is configuration, not a password, but keeping environment-specific IDs outside source avoids accidental cross-app configuration.

## Consent behavior

`StartIoManager` fails closed. If `AdConsentStore` returns `UNKNOWN`, the SDK is not initialized.

When Start.io is configured:

- the first advertising choice is shown before SDK initialization;
- the user can agree or disagree;
- declining does not block TurBot;
- the privacy button remains available so the choice can be changed later;
- each stored decision is sent through Start.io's `pas` consent flag;
- Start.io privacy, service and data-partner links are exposed in the dialog.

This native control is an application-level safety layer. Before production release, verify the final disclosure wording and whether a certified CMP/IAB framework is required for the jurisdictions and ad stack actually used. Do not treat this dialog alone as automatic legal compliance.

No ad should ever be used as the mechanism that blocks the main travel-search or lead flow.

## Before any store release

- confirm the final application ID/package name;
- add icons, screenshots, signing and release metadata;
- verify WebView navigation and external-link handling on physical devices;
- add a user-facing offline/error state;
- complete Google Play Data safety disclosures;
- review the final Start.io disclosure/consent requirements for target jurisdictions;
- review the web content for Play policy compliance;
- publish the exact Start.io dashboard-provided app-ads.txt lines on the developer domain.

## Start.io rollout

See `../docs/startio-monetization.md`.

The intended order is:

1. register the Android app in Start.io;
2. obtain the real App ID;
3. review the consent/CMP surface for production compliance;
4. build with Start.io enabled in test mode;
5. validate analytics, retention and lead conversion;
6. add placements one at a time;
7. only then consider production monetization.
