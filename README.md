# Open Farm Intelligence

Open Farm Intelligence is an open-source **farm-level intelligence and orchestration layer** for Indian agriculture.

It is designed to sit above agricultural data networks and service ecosystems rather than duplicate them.

## Core loop

farm context -> evidence -> hypotheses -> confidence -> next action -> service/human -> field outcome -> feedback

The system treats the farm as a temporal digital twin: land, cultivator, seasonal production relationship, crop cycle, observations and outcomes evolve over time.

## Current MVP

- Temporal farm digital twin
- Seasonal crop-cycle history
- Owner/cultivator/manager/lessor relationships
- Optional production contracts without implying possession
- Case lifecycle and outcome feedback
- Provider registry with capability discovery
- Normalized weather, satellite and soil observations
- Evidence scoring using source reliability, quality, confidence and freshness
- Transparent water-stress and disease-stress hypothesis rules
- Conservative decision states: ask, advise, request test, escalate
- Configurable VISTAAR/Beckn-style service adapter
- Automated pytest CI

## Architecture

Farm Digital Twin
       |
   Farm Snapshot
       |
Provider Registry
 |-- Weather
 |-- Satellite
 |-- Soil
 |-- VISTAAR
 |-- future scientific engines
       |
Normalized Observations
       |
Evidence Fusion
       |
Competing Hypotheses
       |
Decision
       |
Service / Human Action
       |
Field Outcome
       +------> Farm Digital Twin

The provider boundary is deliberate: external systems supply evidence or services; OFI owns context, evidence fusion, reasoning, uncertainty and orchestration.

## Important limitation

The current agronomic thresholds are **engineering heuristics, not calibrated agronomic probabilities or professional crop advice**. The intended next step is to connect validated regional scientific models and datasets behind the same interfaces.

## Development direction

1. Scientific evidence/model adapters
2. Geospatial and temporal field analytics
3. Service routing and human escalation
4. VISTAAR ecosystem integration
5. Persistent PostGIS-backed farm twin (schema + repository boundary now defined)
6. AI layer for retrieval, multimodal interpretation, multilingual interaction and adaptive questioning — without making the LLM the source of agronomic truth
