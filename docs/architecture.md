# Architecture

Open Farm Intelligence owns the farm-context and reasoning loop. External datasets, scientific engines and service networks remain replaceable providers.

## Layers

1. Context: farmer, parcel and crop cycle.
2. Evidence: normalized observations with source, quality, confidence and timestamp.
3. Fusion: source reliability, quality, confidence and freshness.
4. Reasoning: competing hypotheses before action.
5. Decision: advise, ask, diagnose, escalate or route.
6. Feedback: reported -> triaged -> actioned -> observed -> resolved/escalated.

The current scoring is an engineering MVP heuristic, not calibrated probability. Deterministic reasoning makes the substrate testable and auditable before AI and scientific models are introduced.


## Provider architecture

Evidence providers implement a common `collect(FarmSnapshot)` contract and return normalized observations. The registry makes providers composable and replaceable. MVP providers are deterministic mocks; production adapters can later connect weather, satellite, soil, VISTAAR and scientific engines without changing the reasoning layer.

The important boundary is `provider -> observation -> evidence fusion`. Providers do not make farmer-facing decisions.


## Evidence normalization

External providers may expose different schemas, units and provenance fields. OFI converts them at the provider boundary into the stable Observation model.

Current normalized domains:

- Weather: rainfall history/forecast, temperature, humidity and wind.
- Satellite: NDVI, NDVI trend, EVI, NDWI and cloud cover.
- Soil: moisture, pH, organic carbon and available N/P/K fields.

Provider-specific identifiers remain in source; quality and confidence remain explicit. The reasoning layer therefore does not depend on a particular API, satellite vendor or government platform.

## Decision safety boundary

The current agronomic rules are transparent heuristics used to test orchestration. They are not calibrated probabilities and do not constitute crop-specific professional advice.

A future scientific engine can replace or augment the rule set while preserving: provider -> normalized observation -> evidence fusion -> hypothesis -> decision.

The decision layer should become progressively more conservative as action risk increases: uncertain evidence should request additional measurement or human/scientific review rather than manufacture certainty.
