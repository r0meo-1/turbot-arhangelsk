package ru.r0meo1.turbot;

import org.junit.Test;
import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.*;
import static org.junit.Assert.*;

/** Fake adapter tests only: no SDK, device, credentials or network delivery. */
public class AdControllerTest {
    static final class FakeAdapter implements AdController.Adapter {
        Events events;
        int loads, shows, cancels;
        boolean throwLoad, throwShow, throwCancel;
        Runnable duringShow = () -> { };
        public void load(AdPlacement placement, Events callback) {
            loads++;
            events = callback;
            if (throwLoad) throw new IllegalStateException("offline");
        }
        public void show() {
            shows++;
            duringShow.run();
            if (throwShow) throw new IllegalStateException("show failure");
        }
        public void cancel() {
            cancels++;
            if (throwCancel) throw new IllegalStateException("cancel failure");
        }
    }

    static final class Fixture implements AdController.Gates {
        final FakeAdapter adapter = new FakeAdapter();
        long now = 1_000_000, last = Long.MIN_VALUE;
        int impressions, rewards;
        boolean enabled = true, hasId = true, initialized = true, consentApplied = true;
        AdConsentStore.Decision consent = AdConsentStore.Decision.GRANTED;
        AdConsentStore.Decision applied = AdConsentStore.Decision.GRANTED;
        final AdController controller = new AdController(adapter, this, () -> now, () -> rewards++);
        Fixture() {
            controller.setForeground(true);
            controller.setFlow(AdFlow.ACTION_COMPLETED);
        }
        public boolean eligible(AdPlacement placement, long time) {
            return AdDeliveryPolicy.eligible(enabled && hasId, initialized,
                    consentApplied, consent, applied)
                    && AdFrequencyPolicy.elapsed(last, time, placement.minimumIntervalMs());
        }
        public void impression(AdPlacement placement, long time) {
            impressions++;
            if (placement.minimumIntervalMs() > 0) last = time;
        }
        void ready() {
            assertTrue(controller.request(AdPlacement.POST_ACTION_INTERSTITIAL, false));
            adapter.events.loaded();
            assertEquals(AdController.State.READY, controller.state());
        }
    }

    @Test public void protectedFlowsNeverLoad() {
        for (AdFlow flow : AdFlow.values()) {
            if (flow == AdFlow.RESULTS_VISIBLE || flow == AdFlow.ACTION_COMPLETED) continue;
            Fixture f = new Fixture();
            f.controller.setFlow(flow);
            for (AdPlacement placement : AdPlacement.values()) {
                assertFalse(f.controller.request(placement, true));
            }
            assertEquals(0, f.adapter.loads);
        }
    }

    @Test public void configurationAndConsentFailuresNeverLoadOrShow() {
        for (int gate = 0; gate < 5; gate++) {
            Fixture f = new Fixture();
            f.ready();
            switch (gate) {
                case 0: f.enabled = false; break;
                case 1: f.hasId = false; break;
                case 2: f.initialized = false; break;
                case 3: f.consentApplied = false; break;
                default: f.consent = AdConsentStore.Decision.UNKNOWN;
            }
            assertFalse(f.controller.showIfReady(false));
            assertFalse(f.controller.request(AdPlacement.POST_ACTION_INTERSTITIAL, false));
            assertEquals(0, f.adapter.shows);
        }
        Fixture denied = new Fixture();
        denied.consent = AdConsentStore.Decision.DENIED;
        denied.applied = AdConsentStore.Decision.DENIED;
        denied.ready();
        assertTrue(denied.controller.showIfReady(false));
    }

    @Test public void loadNeverAutomaticallyShowsAndLoadingDoesNotWait() {
        Fixture f = new Fixture();
        assertTrue(f.controller.request(AdPlacement.POST_ACTION_INTERSTITIAL, false));
        assertEquals(AdController.State.LOADING, f.controller.state());
        assertFalse(f.controller.showIfReady(false));
        f.adapter.events.loaded();
        assertEquals(0, f.adapter.shows);
        assertEquals(0, f.impressions);
        assertTrue(f.controller.showIfReady(false));
        assertEquals(0, f.impressions);
        f.adapter.events.impression();
        f.adapter.events.impression();
        assertEquals(1, f.impressions);
        f.adapter.events.dismissed();
        assertEquals(AdController.State.DISMISSED, f.controller.state());
        assertFalse(f.controller.request(AdPlacement.POST_ACTION_INTERSTITIAL, false));
    }

    @Test public void frequencyRecheckedAtDisplayAndExactBoundary() {
        Fixture f = new Fixture();
        f.ready();
        f.last = f.now;
        assertFalse(f.controller.showIfReady(false));
        f.now += 239_999;
        assertFalse(f.controller.request(AdPlacement.POST_ACTION_INTERSTITIAL, false));
        f.now++;
        f.ready();
        assertTrue(f.controller.showIfReady(false));
        assertFalse(AdFrequencyPolicy.elapsed(100, 99, 240_000));
        assertFalse(AdFrequencyPolicy.elapsed(-10, Long.MAX_VALUE, 240_000));
        assertTrue(AdFrequencyPolicy.elapsed(Long.MIN_VALUE, 0, 240_000));
    }

    @Test public void failuresAndCloseDoNotCountAsImpressionsOrRewards() {
        for (int failure = 0; failure < 4; failure++) {
            Fixture f = new Fixture();
            if (failure == 0) f.adapter.throwLoad = true;
            f.controller.request(AdPlacement.POST_ACTION_INTERSTITIAL, false);
            if (failure == 1) f.adapter.events.failed(); // Offline/no fill/timeout from adapter.
            if (failure >= 2) {
                f.adapter.events.loaded();
                f.adapter.throwShow = failure == 2;
                f.controller.showIfReady(false);
                if (failure == 2) f.adapter.events.failed(); // Confirm cancellation/failure.
                if (failure == 3) f.adapter.events.dismissed(); // close/back before impression
            }
            f.adapter.events.rewardCompleted();
            assertEquals(0, f.impressions);
            assertEquals(0, f.rewards);
            assertEquals(Long.MIN_VALUE, f.last);
            assertTrue(f.controller.state() == AdController.State.FAILED
                    || f.controller.state() == AdController.State.DISMISSED);
        }
    }

    @Test public void navigationBackgroundConsentAndBackInvalidateStaleInventory() {
        for (int change = 0; change < 4; change++) {
          for (boolean loaded : new boolean[] {false, true}) {
            Fixture f = new Fixture();
            assertTrue(f.controller.request(AdPlacement.POST_ACTION_INTERSTITIAL, false));
            if (loaded) f.adapter.events.loaded();
            AdController.Adapter.Events stale = f.adapter.events;
            switch (change) {
                case 0: f.controller.setFlow(AdFlow.SEARCH_LOADING); break;
                case 1: f.controller.setForeground(false); break;
                case 2: f.consent = AdConsentStore.Decision.DENIED;
                    f.applied = AdConsentStore.Decision.DENIED; f.controller.invalidate(); break;
                default: f.controller.invalidate(); // navigation/back
            }
            assertFalse(f.controller.showIfReady(false));
            f.controller.setFlow(AdFlow.ACTION_COMPLETED);
            f.controller.setForeground(true);
            f.ready();
            stale.loaded(); stale.failed(); stale.impression(); stale.rewardCompleted(); stale.dismissed();
            assertEquals(AdController.State.READY, f.controller.state());
            assertEquals(0, f.impressions);
          }
        }
    }

    @Test public void rapidAndReentrantTapsCannotDuplicateOrOverlap() {
        Fixture f = new Fixture();
        assertTrue(f.controller.request(AdPlacement.POST_ACTION_INTERSTITIAL, false));
        for (int i = 0; i < 20; i++) {
            assertFalse(f.controller.request(AdPlacement.REWARDED_OPTIONAL, true));
            assertFalse(f.controller.showIfReady(true));
        }
        f.adapter.events.loaded();
        assertFalse(f.controller.request(AdPlacement.REWARDED_OPTIONAL, true));
        f.adapter.duringShow = () -> {
            assertFalse(f.controller.showIfReady(true));
            assertFalse(f.controller.request(AdPlacement.REWARDED_OPTIONAL, true));
        };
        assertTrue(f.controller.showIfReady(false));
        f.adapter.throwCancel = true;
        f.controller.setForeground(false);
        f.controller.setForeground(true);
        assertFalse(f.controller.request(AdPlacement.REWARDED_OPTIONAL, true));
        // Resume must not make an invalidated showing ad rewardable or redisplayable.
        f.adapter.events.impression();
        assertEquals(1, f.impressions);
        f.adapter.events.dismissed();
        assertEquals(AdController.State.DISMISSED, f.controller.state());
        assertEquals(1, f.adapter.loads);
        assertEquals(1, f.adapter.shows);
    }

    @Test public void rewardedRequiresFreshTapAndVerifiedCompletionExactlyOnce() {
        Fixture f = new Fixture();
        assertFalse(f.controller.request(AdPlacement.REWARDED_OPTIONAL, false));
        assertTrue(f.controller.request(AdPlacement.REWARDED_OPTIONAL, true));
        f.adapter.events.loaded();
        assertFalse(f.controller.showIfReady(false));
        assertTrue(f.controller.request(AdPlacement.REWARDED_OPTIONAL, true));
        f.adapter.events.loaded();
        assertTrue(f.controller.showIfReady(true));
        f.adapter.events.rewardCompleted();
        assertEquals(0, f.rewards);
        f.adapter.events.impression();
        assertEquals(0, f.rewards);
        f.adapter.events.rewardCompleted(); f.adapter.events.rewardCompleted();
        assertEquals(1, f.rewards);
        f.adapter.events.dismissed(); f.adapter.events.rewardCompleted();
        assertEquals(1, f.rewards);
    }

    @Test public void consentChangeDuringRewardedDisplayRejectsCompletion() {
        Fixture f = new Fixture();
        f.controller.request(AdPlacement.REWARDED_OPTIONAL, true);
        f.adapter.events.loaded(); f.controller.showIfReady(true); f.adapter.events.impression();
        f.controller.invalidate();
        f.adapter.events.rewardCompleted();
        assertEquals(0, f.rewards);
    }

    @Test public void consentMustMatchSuccessfullySubmittedDecision() {
        Fixture f = new Fixture();
        f.ready();
        f.consent = AdConsentStore.Decision.DENIED;
        assertFalse(f.controller.showIfReady(false));
        assertFalse(AdDeliveryPolicy.configured(true, null));
        assertFalse(AdDeliveryPolicy.configured(true, "  "));
        assertFalse(AdDeliveryPolicy.configured(true, ""));
        assertFalse(AdDeliveryPolicy.eligible(true, true, true, null, null));
    }

    @Test public void slowLoadAndOldReadyInventoryExpireWithoutWaitingOrAutoRetry() {
        for (boolean ready : new boolean[] {false, true}) {
            Fixture f = new Fixture();
            f.controller.request(AdPlacement.POST_ACTION_INTERSTITIAL, false);
            if (ready) f.adapter.events.loaded();
            AdController.Adapter.Events old = f.adapter.events;
            f.now += 30_000;
            assertFalse(f.controller.showIfReady(false));
            old.loaded();
            assertEquals(AdController.State.IDLE, f.controller.state());
            assertEquals(1, f.adapter.loads);
            assertEquals(0, f.adapter.shows);
            f.ready();
            old.failed();
            assertEquals(AdController.State.READY, f.controller.state());
        }
    }

    @Test public void thrownShowQuarantinesDisplayUntilConfirmedTerminalEvent() {
        Fixture f = new Fixture();
        f.ready();
        f.adapter.throwShow = true;
        assertFalse(f.controller.showIfReady(false));
        assertEquals(AdController.State.SHOWING, f.controller.state());
        assertFalse(f.controller.request(AdPlacement.REWARDED_OPTIONAL, true));
        f.adapter.events.failed();
        assertEquals(AdController.State.FAILED, f.controller.state());
        assertEquals(0, f.impressions);
    }

    @Test public void concurrentTapRacesStartOnlyOneLoadAndOneDisplay() throws Exception {
        Fixture f = new Fixture();
        ExecutorService pool = Executors.newFixedThreadPool(8);
        try {
            for (boolean display : new boolean[] {false, true}) {
                CountDownLatch start = new CountDownLatch(1);
                List<Future<Boolean>> attempts = new ArrayList<>();
                for (int i = 0; i < 32; i++) {
                    attempts.add(pool.submit(() -> {
                        start.await();
                        return display ? f.controller.showIfReady(false)
                                : f.controller.request(AdPlacement.POST_ACTION_INTERSTITIAL, false);
                    }));
                }
                start.countDown();
                int accepted = 0;
                for (Future<Boolean> attempt : attempts) {
                    if (attempt.get(5, TimeUnit.SECONDS)) accepted++;
                }
                assertEquals(1, accepted);
                if (!display) f.adapter.events.loaded();
            }
            assertEquals(1, f.adapter.loads);
            assertEquals(1, f.adapter.shows);
        } finally {
            pool.shutdownNow();
        }
    }
}
