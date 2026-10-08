# Open Farm Intelligence

**Open Farm Intelligence (OFI)** is an open-source farm-level intelligence and orchestration layer for Indian agriculture.

> **Bharat-VISTAAR provides the agricultural digital network; Open Farm Intelligence provides the farm-level intelligence and orchestration layer that turns that network into a continuous farm decision system.**

OFI is intentionally **not another farmer chatbot**. It maintains farm context, gathers and evaluates evidence, represents uncertainty, chooses the next action, routes work to services or experts, and learns from field outcomes.

## Core loop

```text
Farm context
    ↓
Evidence
    ↓
Competing hypotheses
    ↓
Confidence + uncertainty
    ↓
Next action
    ↓
Service / human execution
    ↓
Field outcome
    ↓
Feedback into the farm record
```

## Architecture

```text
                     FARM DIGITAL TWIN
                            │
                            ▼
                  ┌───────────────────┐
                  │ OFI ORCHESTRATOR  │
                  │ context           │
                  │ evidence          │
                  │ reasoning         │
                  │ uncertainty       │
                  │ decision          │
                  └─────────┬─────────┘
                            │
             ┌──────────────┼──────────────┐
             ▼              ▼              ▼
        Data providers   VISTAAR       Science models
        weather/soil/    services      agronomy/
        satellite                       water balance
             │              │              │
             └──────────────┼──────────────┘
                            ▼
                     Farm decision
                            │
                            ▼
                    Service / human
                            │
                            ▼
                       Field result
                            │
                            └──────► feedback
```

The provider boundary is deliberate: external systems supply evidence or services; OFI owns farm context, evidence fusion, reasoning, uncertainty and orchestration.

## Current capabilities

- Temporal farm digital twin with parcel and crop-cycle context
- Separate owner, cultivator and production-contract relationships
- Case lifecycle with append-only events and optimistic versioning
- Weather, satellite and soil provider interfaces
- Evidence normalization, source reliability and freshness scoring
- Spatial and temporal evidence-alignment checks
- Transparent water-stress and disease-stress hypotheses
- Conservative decisions: ask, advise, request test, escalate
- Scientific-model registry with an FAO-56-style water-balance screening model
- Deterministic service capability discovery and action routing
- Service transactions with explicit lifecycle transitions
- Consent, actor binding and idempotent execution gateway
- Outcome feedback and empirical experience memory
- Optional PostgreSQL/PostGIS persistence boundary
- Automated pytest CI

## Repository layout

```text
src/ofi/
├── domain/          # Farm, crop, observation, case and decision models
├── twin/            # Farm digital twin + optional PostGIS persistence
├── providers/       # Provider contracts, adapters and normalization
├── geospatial/      # Parcel/observation alignment and field analytics
├── science/         # Scientific model interfaces and water-balance model
├── intelligence/    # Evidence fusion, confidence and orchestration
└── services/        # Cases, actions, service discovery, execution, feedback

tests/               # Unit and closed-loop regression tests
```

## Quick start

Requires Python 3.11+.

```bash
git clone https://github.com/vkraj1123/Open-farm-intelligence.git
cd Open-farm-intelligence

python -m pip install -e ".[dev]"
pytest -q
```

Run the API:

```bash
uvicorn ofi.api:app --reload
```

## Persistence

The domain is repository-driven. The default in-memory farm twin is lightweight for tests and local development.

An optional PostGIS implementation is available as `ofi.twin.postgis.PostGISFarmTwinStore`. Install the optional PostgreSQL dependency and apply `src/ofi/twin/schema.sql` to a PostgreSQL database with PostGIS enabled.

The PostGIS adapter accepts an injected connection factory so credentials, pooling, TLS, retries and deployment-specific connection management remain outside the domain.

## Scientific safety boundary

Current agronomic thresholds are **engineering heuristics and screening models, not calibrated agronomic probabilities or professional crop advice**.

The intended production path is to place validated regional scientific models and datasets behind the existing interfaces. An LLM should help with retrieval, multilingual interaction, multimodal interpretation and adaptive questioning, but it should not become the source of agronomic truth.

## Relationship to Bharat-VISTAAR

OFI is designed to complement India's agricultural digital public infrastructure rather than replace it.

Potential future integration points include:

- VISTAAR/Beckn-style provider discovery
- ICAR/KVK scientific evidence and expert escalation
- IMD weather services
- AgMarkNet/market information
- Soil Health Card and soil-test evidence
- State agriculture systems and schemes
- Service providers such as soil-testing, diagnostics and extension

External integrations will remain behind explicit provider/service interfaces so that the core intelligence layer stays testable and provider-neutral.

## Roadmap

1. Harden deterministic provider-selection policy
2. Complete PostgreSQL/PostGIS integration tests
3. Establish atomic unit-of-work boundaries across farm twin and case state
4. Add durable event sequencing and idempotent external callbacks
5. Validate/calibrate scientific models with Rajasthan-local data
6. Add VISTAAR/Beckn-compatible service adapters
7. Add multimodal and multilingual AI capabilities downstream of the evidence/science substrate
8. Build production observability, consent scopes and governance controls

## Status

This repository is an **active engineering prototype**. The architecture and interfaces are the primary focus at this stage; external agricultural integrations and production agronomic validation are intentionally not claimed yet.

## License

See the repository license file for current licensing terms.
