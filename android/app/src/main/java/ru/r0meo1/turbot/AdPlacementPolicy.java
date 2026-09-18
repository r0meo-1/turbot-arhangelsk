package ru.r0meo1.turbot;

final class AdPlacementPolicy {
    private AdPlacementPolicy() {}

    static boolean mayRequest(AdPlacement placement, boolean userInitiated) {
        switch (placement) {
            case RESULTS_NATIVE:
                return true;
            case POST_ACTION_INTERSTITIAL:
                return true;
            case REWARDED_OPTIONAL:
                return userInitiated;
            case RETURN_AD:
                // Disabled for the initial rollout even though the placement is reserved.
                return false;
            default:
                return false;
        }
    }
}
