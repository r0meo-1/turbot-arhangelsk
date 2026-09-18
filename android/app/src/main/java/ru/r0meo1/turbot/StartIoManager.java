package ru.r0meo1.turbot;

import android.app.Activity;

import com.startapp.sdk.adsbase.StartAppSDK;

final class StartIoManager {
    private static boolean initialized;
    private static boolean consentApplied;
    private static AdConsentStore.Decision appliedDecision = AdConsentStore.Decision.UNKNOWN;

    private StartIoManager() {}

    static boolean isConfigured() {
        return AdDeliveryPolicy.configured(BuildConfig.STARTIO_ENABLED, BuildConfig.STARTIO_APP_ID);
    }

    static boolean initializeIfAllowed(Activity activity) {
        consentApplied = false;
        if (!isConfigured()) {
            return false;
        }

        AdConsentStore.Decision decision = AdConsentStore.read(activity);
        if (decision == AdConsentStore.Decision.UNKNOWN) {
            return false;
        }

        try {
            if (!initialized) {
                // Return ads stay off. Splash ads are disabled in AndroidManifest.xml.
                StartAppSDK.init(activity, BuildConfig.STARTIO_APP_ID, false);
                initialized = true;
            }

            submitConsent(activity, decision);
            appliedDecision = decision;
            consentApplied = true;
            return true;
        } catch (RuntimeException sdkFailure) {
            // Advertising must never interrupt loading TurBot or saving privacy choices.
            return false;
        }
    }

    static boolean mayRequest(
            Activity activity, AdPlacement placement, AdFlow flow,
            boolean userInitiated, boolean foreground, long nowMs
    ) {
        return AdPlacementPolicy.mayRequest(placement, flow, userInitiated, foreground)
                && AdDeliveryPolicy.eligible(isConfigured(), initialized, consentApplied,
                        AdConsentStore.read(activity), appliedDecision)
                && AdFrequencyGate.canShow(activity, placement, nowMs);
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
