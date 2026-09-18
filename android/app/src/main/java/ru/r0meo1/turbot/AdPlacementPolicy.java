package ru.r0meo1.turbot;

final class AdPlacementPolicy {
    private AdPlacementPolicy() {}

    static boolean mayRequest(AdPlacement placement, boolean userInitiated) {
        // Legacy callers have no trusted flow context: fail closed.
        return mayRequest(placement, AdFlow.UNKNOWN, userInitiated, false);
    }

    /** Use again at display time; a preload is not permission to show later. */
    static boolean mayRequest(
            AdPlacement placement, AdFlow flow, boolean userInitiated, boolean foreground
    ) {
        if (placement == null || flow == null || !foreground) {
            return false;
        }
        if (flow != AdFlow.RESULTS_VISIBLE && flow != AdFlow.ACTION_COMPLETED) {
            return false;
        }
        switch (placement) {
            case RESULTS_NATIVE:
                return flow == AdFlow.RESULTS_VISIBLE;
            case POST_ACTION_INTERSTITIAL:
                return flow == AdFlow.ACTION_COMPLETED;
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
