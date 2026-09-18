package ru.r0meo1.turbot;

/** Native-owned flow state. Unknown WebView state must never permit ads. */
enum AdFlow {
    UNKNOWN,
    ONBOARDING,
    SEARCH_PARAMETERS,
    SEARCH_LOADING,
    RESULTS_VISIBLE,
    LEAD_CONTACT_SUBMISSION,
    ACTION_COMPLETED
}
