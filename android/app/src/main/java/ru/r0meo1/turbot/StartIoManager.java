package ru.r0meo1.turbot;

import android.app.Activity;

import com.startapp.sdk.adsbase.StartAppSDK;

final class StartIoManager {
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

        // Return ads stay off. Splash ads are disabled in AndroidManifest.xml.
        StartAppSDK.init(activity, BuildConfig.STARTIO_APP_ID, false);
        StartAppSDK.setUserConsent(
                activity,
                "pas",
                System.currentTimeMillis(),
                decision == AdConsentStore.Decision.GRANTED
        );
        return true;
    }
}
