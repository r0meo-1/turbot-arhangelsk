package ru.r0meo1.turbot;

/** Serialized lifecycle independent of Android and SDK credentials. No automatic show/retry. */
final class AdController {
    private static final long INVENTORY_LIFETIME_MS = 30_000L;
    enum State { IDLE, LOADING, READY, SHOWING, DISMISSED, FAILED }

    interface Gates {
        // Must read current configuration, initialization, stored/applied consent and frequency.
        boolean eligible(AdPlacement placement, long nowMs);
        void impression(AdPlacement placement, long nowMs);
    }

    interface Adapter {
        interface Events {
            void loaded();
            void failed();
            void impression();
            void dismissed();
            // Only the SDK's verified video-completion event may call this.
            void rewardCompleted();
        }
        void load(AdPlacement placement, Events events);
        void show();
        // Release pending inventory. If showing, dismiss and eventually report dismissed/failed.
        void cancel();
    }

    interface Clock { long nowMs(); }

    private final Adapter adapter;
    private final Gates gates;
    private final Clock clock;
    private final Runnable reward;
    private State state = State.IDLE;
    private AdFlow flow = AdFlow.UNKNOWN;
    private boolean foreground;
    private AdPlacement placement;
    private long generation;
    private boolean valid;
    private boolean impressed;
    private boolean rewarded;
    private long requestedAt;

    AdController(Adapter adapter, Gates gates, Clock clock, Runnable reward) {
        this.adapter = adapter;
        this.gates = gates;
        this.clock = clock;
        this.reward = reward;
    }

    synchronized State state() { return state; }

    /** May also be called by a future adapter's timeout scheduler; never expires a display. */
    synchronized void expirePending() {
        if (state != State.LOADING && state != State.READY) return;
        long now = clock.nowMs();
        long age = now - requestedAt;
        if (now < requestedAt || age < 0 || age >= INVENTORY_LIFETIME_MS) invalidate();
    }

    synchronized void setFlow(AdFlow next) {
        if (flow != next) invalidate();
        flow = next == null ? AdFlow.UNKNOWN : next;
    }

    synchronized void setForeground(boolean next) {
        if (foreground != next) invalidate();
        foreground = next;
    }

    /** Navigation, consent changes (including failed submission), back and destruction. */
    synchronized void invalidate() {
        valid = false;
        if (state != State.SHOWING) {
            ++generation;
            state = State.IDLE;
        }
        // A showing ad retains its slot until a terminal callback; cancellation isn't dismissal.
        try { adapter.cancel(); } catch (RuntimeException ignored) { /* fail closed */ }
    }

    private boolean eligible(boolean explicitTap) {
        try {
            return AdPlacementPolicy.mayRequest(placement, flow, explicitTap, foreground)
                    && gates.eligible(placement, clock.nowMs());
        } catch (RuntimeException failure) {
            return false;
        }
    }

    /** Returns immediately; a primary action must never depend on this result or callback. */
    synchronized boolean request(AdPlacement next, boolean explicitTap) {
        expirePending();
        if (state == State.LOADING || state == State.READY || state == State.SHOWING) return false;
        placement = next;
        if (!eligible(explicitTap)) return false;
        long ticket = ++generation;
        valid = true;
        impressed = rewarded = false;
        requestedAt = clock.nowMs();
        state = State.LOADING;
        try {
            adapter.load(placement, new Adapter.Events() {
                public void loaded() { onLoaded(ticket); }
                public void failed() { onTerminal(ticket, State.FAILED); }
                public void impression() { onImpression(ticket); }
                public void dismissed() { onTerminal(ticket, State.DISMISSED); }
                public void rewardCompleted() { onReward(ticket); }
            });
        } catch (RuntimeException failure) { onTerminal(ticket, State.FAILED); }
        return state != State.FAILED;
    }

    /** No waiting for loading; rewarded display requires a fresh explicit tap. */
    synchronized boolean showIfReady(boolean explicitTap) {
        expirePending();
        if (state != State.READY) return false;
        if (!valid || !eligible(explicitTap)) { invalidate(); return false; }
        state = State.SHOWING; // Set before entering adapter to contain rapid/reentrant taps.
        long ticket = generation;
        try { adapter.show(); }
        catch (RuntimeException failure) {
            // A thrown show call does not prove the SDK never opened a window.
            if (ticket == generation && state == State.SHOWING) invalidate();
            return false;
        }
        return valid && state == State.SHOWING;
    }

    private synchronized void onLoaded(long ticket) {
        expirePending();
        if (ticket == generation && valid && state == State.LOADING) state = State.READY;
    }

    private synchronized void onTerminal(long ticket, State terminal) {
        if (ticket != generation || (state != State.LOADING && state != State.READY
                && state != State.SHOWING)) return;
        state = terminal;
        valid = false;
        ++generation;
        try { adapter.cancel(); } catch (RuntimeException ignored) { /* no retry */ }
    }

    private synchronized void onImpression(long ticket) {
        if (ticket != generation || state != State.SHOWING || impressed) return;
        impressed = true;
        // A confirmed impression still counts if safety changed while the SDK was dismissing.
        try { gates.impression(placement, clock.nowMs()); }
        catch (RuntimeException failure) { invalidate(); }
    }

    private synchronized void onReward(long ticket) {
        if (ticket != generation || !valid || state != State.SHOWING || !impressed || rewarded
                || placement != AdPlacement.REWARDED_OPTIONAL || !eligible(true)) return;
        rewarded = true;
        try { reward.run(); } catch (RuntimeException ignored) { /* never repeat a reward */ }
    }
}
