package ru.r0meo1.turbot;

final class AdFrequencyPolicy {
    private AdFrequencyPolicy() { }

    static boolean elapsed(long lastShown, long nowMs, long intervalMs) {
        if (intervalMs <= 0 || lastShown == Long.MIN_VALUE) return true;
        // Clock rollback or overflow must not open the gate.
        return nowMs >= lastShown && nowMs - lastShown >= 0
                && nowMs - lastShown >= intervalMs;
    }
}
