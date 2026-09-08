# M2C.5 Golden Fixtures

Status: **FROZEN**

Schema version: `m2c5-v1`

The M2C.5 fixtures freeze deterministic bridge inputs and expected outcomes before M2C.6 wires the full offline bridge into the recommendation pipeline.

## Scenarios

1. `happy_path.json`
   - three canonical candidates
   - confirmed commercial prices
   - no precomputed `signals` in input
   - exact expected feature signals from `hotel-features-v1.0`
   - expected pipeline level: `healthy`

2. `partial_pricing.json`
   - four canonical content candidates
   - two confirmed prices
   - two explicit `null` prices
   - missing prices remain unknown and are never synthesized
   - expected pipeline level: `partial`

3. `provider_timeout.json`
   - commercial provider timeout affects the whole candidate set
   - outage is represented as one provider-level degradation event
   - no duplicated candidate-level `price_missing` events
   - expected pipeline level: `blocked`

4. `identity_mismatch.json`
   - Tripadvisor content identity resolves to a canonical hotel
   - pricing provider reference is absent from the identity snapshot
   - runtime join is blocked
   - no fuzzy matching and no synthetic price

## Golden rule

```text
Same fixture schema
+ Same feature rules version
+ Same deterministic bridge implementation
=
Same eligible set
+ Same signals
+ Same degradation outcome
```

Fixture inputs must never contain precomputed scoring signals. Any change to expected signals or degradation outcomes must be explicit and accompanied by a rationale in the commit message or review.

M2C.6 is allowed to consume these fixtures, but it must not silently rewrite their expected outcomes.
