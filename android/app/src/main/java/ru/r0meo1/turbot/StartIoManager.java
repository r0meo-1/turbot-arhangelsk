package ru.r0meo1.turbot;

import android.app.Activity;

import com.startapp.sdk.adsbase.StartAppSDK;

final class StartIoManager {
    private static boolean initialized;

    private StartIoManager() {}

    static boolean isConfigured() {
        return BuildConfig.STARTIO_ENABLED
                && BuildConfig.STARTIO_APP_ID != null
                && !BuildConfig.STARTIO_APP_ID.trim().isEmpty();
    }

    static boolean initializeIfAllowed(Activity activity) {
        if (!isConfigured()) {
            return false;
        }

        AdConsentStore.Decision decision = AdConsentStore.read(activity);
        if (decision == AdConsentStore.Decision.UNKNOWN) {
            return false;
        }

        if (!initialized) {
            // Return ads stay off. Splash ads are disabled in AndroidManifest.xml.
            StartAppSDK.init(activity, BuildConfig.STARTIO_APP_ID, false);
            initialized = true;
        }

        submitConsent(activity, decision);
        return true;
    }

    static boolean applyCurrentConsent(Activity activity) {
        return initializeIfAllowed(activity);
    }

    private static void submitConsent(
            Activity activity,
            AdConsentStore.Decision decision
    ) {
        StartAppSDK.setUserConsent(
                activity,
                "pas",
                System.currentTimeMillis(),
                decision == AdConsentStore.Decision.GRANTED
        );
    }
}
