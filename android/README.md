# TurBot Android shell

Minimal native Android shell for the existing TurBot web experience.

## Status

The shell is intentionally **ad-free** for now. Start.io is not initialized until a real Start.io App ID, consent flow, store listing and production privacy disclosure are ready.

## Current baseline

- package: `ru.r0meo1.turbot`
- target SDK: 36
- compile SDK: 36
- minimum SDK: 24
- Android Gradle Plugin: 9.4.0
- Gradle: 9.6.0
- Java: 17
- first-party entry point: `https://r0meo1.ru/apreltour/`
- cleartext HTTP: disabled
- file/content access from WebView: disabled
- non-first-party links: opened in the system browser

## Local setup

1. Install Android Studio with Android SDK 36 and JDK 17.
2. If wrapper scripts/JAR are not present, run:
   `gradle wrapper --gradle-version 9.6.0`
3. Open the `android/` directory in Android Studio.
4. Build/install the debug app.

The repository stores `gradle-wrapper.properties`, but not the generated wrapper JAR.

## Before any store release

- confirm the final application ID/package name;
- add icons, screenshots, signing and release metadata;
- verify WebView navigation and external-link handling on physical devices;
- add a user-facing offline/error state;
- complete Google Play Data safety disclosures;
- review the web content for Play policy compliance.

## Start.io activation

Do not add a fake App ID. When the native app is registered in Start.io:

1. follow `../docs/startio-monetization.md`;
2. pin the then-current Start.io SDK;
3. add the exact real App ID via non-secret build configuration;
4. implement GDPR/other applicable consent before personalized advertising;
5. keep Start.io splash/return ads disabled initially;
6. add ads only at the placements defined in the monetization document;
7. publish the exact dashboard-provided app-ads.txt entries on the developer domain;
8. run integration tests before enabling production ads.
