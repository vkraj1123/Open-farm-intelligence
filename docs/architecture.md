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
