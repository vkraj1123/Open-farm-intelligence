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
