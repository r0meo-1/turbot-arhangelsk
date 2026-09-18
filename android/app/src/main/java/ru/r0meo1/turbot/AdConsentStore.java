package ru.r0meo1.turbot;

import android.content.Context;
import android.content.SharedPreferences;

final class AdConsentStore {
    enum Decision {
        UNKNOWN,
        GRANTED,
        DENIED
    }

    private static final String PREFS_NAME = "turbot_ad_privacy";
    private static final String KEY_PERSONALIZED_ADS = "personalized_ads_consent";

    private AdConsentStore() {}

    static Decision read(Context context) {
        SharedPreferences preferences =
                context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE);
        if (!preferences.contains(KEY_PERSONALIZED_ADS)) {
            return Decision.UNKNOWN;
        }
        return preferences.getBoolean(KEY_PERSONALIZED_ADS, false)
                ? Decision.GRANTED
                : Decision.DENIED;
    }

    static void write(Context context, Decision decision) {
        if (decision == Decision.UNKNOWN) {
            context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
                    .edit()
                    .remove(KEY_PERSONALIZED_ADS)
                    .apply();
            return;
        }

        context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
                .edit()
                .putBoolean(KEY_PERSONALIZED_ADS, decision == Decision.GRANTED)
                .apply();
    }
}
