package ru.r0meo1.turbot;

enum AdPlacement {
    RESULTS_NATIVE("results_native", 0L),
    POST_ACTION_INTERSTITIAL("post_action_interstitial", 4 * 60_000L),
    REWARDED_OPTIONAL("rewarded_optional", 0L),
    RETURN_AD("return_ad", 30 * 60_000L);

    private final String id;
    private final long minimumIntervalMs;

    AdPlacement(String id, long minimumIntervalMs) {
        this.id = id;
        this.minimumIntervalMs = minimumIntervalMs;
    }

    String id() {
        return id;
    }

    long minimumIntervalMs() {
        return minimumIntervalMs;
    }
}
