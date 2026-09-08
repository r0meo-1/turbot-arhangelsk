# M2C.4 Deterministic Feature Extraction

Status: **IMPLEMENTED / acceptance pending CI**

Rules version: `hotel-features-v1.0`
Scoring contract: `hotel-v1.0`

## Boundary

The feature layer accepts normalized factual input only. It never accepts provider-supplied or fixture-supplied `signals`.

```text
Normalized content facts
+ Canonical commercial facts
+ Validated request
        ↓
Deterministic feature extraction
        ↓
6 signals in [0, 100]
        ↓
hotel-v1.0 scoring
```

No network, AI, fuzzy identity resolution or hidden defaults are allowed.

## Required signals

- `budget`
- `beach_location`
- `service`
- `reviews`
- `traveler_fit`
- `preferences`

The produced key set must exactly match `hotel_recommendation.config.WEIGHTS`.

## Rules v1

### Budget

- price above request budget -> `0`
- price exactly at budget ceiling -> `70`
- score increases linearly with budget headroom
- price at or below 75% of budget -> `100`

This is a value signal, not the hard budget filter. Hard eligibility remains upstream.

### Beach/location

Deterministic distance bands:

- <= 100m -> 100
- <= 300m -> 95
- <= 500m -> 85
- <= 1000m -> 75
- <= 1500m -> 65
- > 1500m -> 0

### Service

`service` requires an explicit factual `service_rating` on the Tripadvisor 1–5 scale. Overall hotel rating is not silently substituted for missing service quality.

The observed 1–5 scale is mapped linearly to 0–100.

### Reviews

`reviews` combines two observed facts:

- 75% Tripadvisor overall rating quality
- 25% review-volume confidence

Review volume bands are frozen in code and therefore deterministic.

### Traveler fit

For a request with children:

- `family_friendly = true` -> 100
- `family_friendly = false` -> 0

For an adults-only request, no family constraint is applied -> 100.

### Preferences

Requested preferences are normalized to lowercase exact keys and compared against factual normalized attributes. Built-in factual keys:

- `breakfast` from the breakfast fact
- `family` from the family-friendly fact
- `beach` when beach distance satisfies the request limit (or 1500m when no explicit limit exists)
- normalized amenity keys such as `pool`

Score = exact matched requested preferences / total requested preferences.

No fuzzy matching is performed.

## Invalid input

Feature extraction refuses invalid normalized facts rather than repairing them. Examples:

- currency mismatch
- non-positive price/budget
- invalid Tripadvisor/service rating
- negative beach distance or review count
- invalid child age

`UNKNOWN != default`. In particular, missing service quality must not become an invented numeric score.

## Acceptance gate

```text
Same normalized facts
+ Same validated request
+ hotel-features-v1.0
=
Same exact signals
```

M2C.4 remains isolated from the VS0 orchestration until M2C.5 golden fixtures and M2C.6 offline bridge E2E explicitly replace fixture-authored signals.
