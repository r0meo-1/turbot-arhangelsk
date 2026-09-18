package ru.r0meo1.turbot;

import org.junit.Test;
import static org.junit.Assert.*;

public class AdPlacementPolicyTest {
    @Test public void criticalFlowsBlockEveryPlacementEvenWithExplicitTap() {
        for (AdFlow flow : new AdFlow[] {AdFlow.UNKNOWN, AdFlow.ONBOARDING,
                AdFlow.SEARCH_PARAMETERS, AdFlow.SEARCH_LOADING,
                AdFlow.LEAD_CONTACT_SUBMISSION}) {
            for (AdPlacement placement : AdPlacement.values()) {
                assertFalse(flow + "/" + placement,
                        AdPlacementPolicy.mayRequest(placement, flow, true, true));
            }
        }
    }

    @Test public void placementsRequireTheirSafeContext() {
        assertTrue(AdPlacementPolicy.mayRequest(AdPlacement.RESULTS_NATIVE,
                AdFlow.RESULTS_VISIBLE, false, true));
        assertFalse(AdPlacementPolicy.mayRequest(AdPlacement.RESULTS_NATIVE,
                AdFlow.ACTION_COMPLETED, false, true));
        assertTrue(AdPlacementPolicy.mayRequest(AdPlacement.POST_ACTION_INTERSTITIAL,
                AdFlow.ACTION_COMPLETED, false, true));
        assertFalse(AdPlacementPolicy.mayRequest(AdPlacement.POST_ACTION_INTERSTITIAL,
                AdFlow.RESULTS_VISIBLE, false, true));
        for (AdFlow flow : AdFlow.values()) {
            assertFalse(AdPlacementPolicy.mayRequest(AdPlacement.RETURN_AD, flow, true, true));
            assertFalse(AdPlacementPolicy.mayRequest(AdPlacement.REWARDED_OPTIONAL, flow, false, true));
        }
        assertTrue(AdPlacementPolicy.mayRequest(AdPlacement.REWARDED_OPTIONAL,
                AdFlow.RESULTS_VISIBLE, true, true));
    }

    @Test public void backgroundAndMissingContextFailClosed() {
        for (AdPlacement placement : AdPlacement.values()) {
            assertFalse(AdPlacementPolicy.mayRequest(placement, true));
            assertFalse(AdPlacementPolicy.mayRequest(placement, AdFlow.RESULTS_VISIBLE, true, false));
            assertFalse(AdPlacementPolicy.mayRequest(placement, null, true, true));
        }
        assertFalse(AdPlacementPolicy.mayRequest(null, AdFlow.RESULTS_VISIBLE, true, true));
    }

    @Test public void interstitialIntervalRemainsFourMinutes() {
        assertEquals(240_000L, AdPlacement.POST_ACTION_INTERSTITIAL.minimumIntervalMs());
    }
}
