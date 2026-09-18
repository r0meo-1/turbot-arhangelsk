package ru.r0meo1.turbot;

import android.content.Context;
import android.content.SharedPreferences;

final class AdFrequencyGate {
    private static final String PREFS_NAME = "turbot_ad_frequency";
    private static final String KEY_PREFIX = "last_shown_";

    private AdFrequencyGate() {}

    static boolean canShow(Context context, AdPlacement placement, long nowMs) {
        long interval = placement.minimumIntervalMs();
        if (interval <= 0L) {
            return true;
        }

        long lastShown = preferences(context).getLong(KEY_PREFIX + placement.id(), Long.MIN_VALUE);
        if (lastShown == Long.MIN_VALUE) {
            return true;
        }

        return nowMs - lastShown >= interval;
    }

    static void markShown(Context context, AdPlacement placement, long nowMs) {
        preferences(context)
                .edit()
                .putLong(KEY_PREFIX + placement.id(), nowMs)
                .apply();
    }

    private static SharedPreferences preferences(Context context) {
        return context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE);
    }
}
