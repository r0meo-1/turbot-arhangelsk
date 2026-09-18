package ru.r0meo1.turbot;

final class AdDeliveryPolicy {
    private AdDeliveryPolicy() { }

    static boolean configured(boolean enabled, String appId) {
        return enabled && appId != null && !appId.trim().isEmpty();
    }

    static boolean eligible(boolean configured, boolean initialized,
            boolean consentApplied, AdConsentStore.Decision stored,
            AdConsentStore.Decision applied) {
        return configured && initialized
                && consentApplied && stored != null && stored != AdConsentStore.Decision.UNKNOWN
                && stored == applied;
    }
}
