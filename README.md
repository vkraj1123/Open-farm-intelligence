# Open Farm Intelligence

[![Deploy to Render](https://render.com/images/deploy-to-render-button.svg)](https://render.com/deploy?repo=https://github.com/vkraj1123/Open-farm-intelligence)

> **Temporary staging only:** the included Free Render Postgres resource expires after 30 days. Do not use this setup for real farmer data or production.

**Open Farm Intelligence (OFI)** is an open-source farm-level intelligence and orchestration system for Indian agriculture.

> **Bharat-VISTAAR provides the agricultural digital network; Open Farm Intelligence provides the farm-level intelligence and orchestration layer that turns that network into a continuous farm decision system.**

OFI is deliberately **not another farmer chatbot**. The project is building the infrastructure that can understand a farm as a changing system, collect and align evidence, reason under uncertainty, choose the next useful action, route that action to a service or human, observe the result, and feed the outcome back into future decisions.

This README is both the project description and the **engineering development tracker**. It records what exists, what is intentionally incomplete, and what must be proven before the system should be considered production-ready.

---

## 1. Project thesis

Agricultural problems are rarely single-variable problems.

A farmer may simultaneously face:

- uncertain weather;
- soil and water constraints;
- crop-stage effects;
- disease or pest pressure;
- changing market prices;
- input availability;
- credit or insurance constraints;
- scheme eligibility;
- tenancy/ownership complexity;
- limited access to expert services;
- incomplete or contradictory information.

A useful intelligence system therefore cannot be built as:

```
Question → LLM → Answer
```

OFI is designed around:

```
Farm context
    ↓
Evidence
    ↓
Alignment + provenance + freshness
    ↓
Hypotheses
    ↓
Confidence + contradiction + uncertainty
    ↓
Next best action
    ↓
Service / expert / farmer action
    ↓
Execution
    ↓
Field outcome
    ↓
Empirical feedback
    ↺
```

The core unit is therefore not a conversation. It is a **farm decision cycle**.

---

# 2. Design goals

## Primary goals

1. **Context before conversation**
   - Maintain a structured representation of the farm, parcels, crop cycles, people, contracts and observations.

2. **Evidence before generation**
   - Reason from explicit observations and scientific models instead of allowing an LLM to invent facts.

3. **Uncertainty as a first-class object**
   - Represent source reliability, freshness, confidence, conflicts and insufficient evidence.

4. **Decision before advice**
   - Determine whether the correct response is to advise, ask for information, request a test, or escalate.

5. **Actionability**
   - A decision should be capable of becoming a service request or human task.

6. **Closed-loop learning**
   - Store outcomes and empirical experience rather than treating every interaction as isolated.

7. **Provider neutrality**
   - External data and service ecosystems should be replaceable through explicit interfaces.

8. **Scientific safety**
   - Scientific models must be distinguishable from heuristics and validated before being used for prescriptive recommendations.

9. **Indian agricultural interoperability**
   - The architecture should be able to connect to systems such as VISTAAR/Beckn-style networks, ICAR/KVKs, IMD, AgMarkNet and state systems without coupling the intelligence core to one provider.

10. **Production-grade boundaries**
    - Persistence, transactions, consent, idempotency, versioning and external execution must be explicit rather than hidden inside business logic.

---

# 3. Non-goals

OFI is **not** intended to:

- replace Bharat-VISTAAR;
- replace ICAR, KVKs or agricultural scientists;
- make unsupported agronomic prescriptions;
- pretend heuristic scores are calibrated probabilities;
- become a generic chatbot with an agriculture prompt;
- automatically execute risky farm actions without appropriate authorization and human/service boundaries;
- treat an LLM as the source of scientific truth;
- claim production readiness before real-world validation.

---

# 4. System architecture

```
                         FARM DIGITAL TWIN
                                │
                                ▼
                 ┌──────────────────────────┐
                 │      OFI ORCHESTRATOR    │
                 │                          │
                 │ Context                 │
                 │ Evidence                │
                 │ Alignment               │
                 │ Hypotheses              │
                 │ Uncertainty              │
                 │ Decision                 │
                 │ Action planning          │
                 └────────────┬─────────────┘
                              │
              ┌───────────────┼────────────────┐
              │               │                │
              ▼               ▼                ▼
        Data Providers     Service Network   Science Engine
        ─────────────     ───────────────    ─────────────
        Weather           VISTAAR/Beckn      FAO-56-style
        Satellite         KVK/extension     water balance
        Soil              Diagnostics       other models
        Sensors           Institutions
        Farmer reports    Experts
              │               │                │
              └───────────────┼────────────────┘
                              ▼
                       FARM DECISION
                              │
                              ▼
                    SERVICE / HUMAN ACTION
                              │
                              ▼
                       FIELD OUTCOME
                              │
                              ▼
                    EXPERIENCE / FEEDBACK
```

## Architectural rule

The intelligence layer owns:

- farm context;
- evidence representation;
- evidence alignment;
- reasoning;
- uncertainty;
- decision policy;
- action planning;
- service selection policy.

External systems own:

- their source data;
- their scientific/operational services;
- execution of services;
- institutional workflows.

This separation allows OFI to remain testable even when external providers are unavailable.

---

# 5. Repository structure

```
Open-farm-intelligence/
│
├── src/ofi/
│   ├── api.py
│   │
│   ├── domain/
│   │   └── models.py
│   │       # Core farm, parcel, crop, observation,
│   │       # case, evidence, decision and outcome models
│   │
│   ├── twin/
│   │   ├── repository.py
│   │   ├── farm_twin.py
│   │   ├── postgis.py
│   │   ├── schema.sql
│   │   ├── migration_registry.py
│   │   ├── migrate.py
│   │   ├── recovery_check.py
│   │   └── migrations/
│   │       ├── 001_initial_postgis_case_service_and_evidence_schema.sql
│   │       └── 002_case_record_version_constraint.sql
│   │       # Farm Digital Twin, ordered SQL migrations and persistence boundary
│   │
│   ├── providers/
│   │   ├── base.py
│   │   ├── registry.py
│   │   ├── adapters.py
│   │   ├── normalizers.py
│   │   └── mock_evidence.py
│   │       # Evidence provider contracts and canonicalization
│   │
│   ├── geospatial/
│   │   ├── spatial.py
│   │   └── analytics.py
│   │       # Parcel alignment and field-level analytics
│   │
│   ├── science/
│   │   ├── models.py
│   │   ├── registry.py
│   │   ├── engine.py
│   │   └── water_balance.py
│   │       # Scientific model interface and current screening model
│   │
│   ├── intelligence/
│   │   ├── alignment.py
│   │   ├── confidence.py
│   │   ├── freshness.py
│   │   ├── fusion.py
│   │   └── orchestrator.py
│   │       # Evidence → hypothesis → decision
│   │
│   └── services/
│       ├── case_repository.py
│       ├── postgres_case_repository.py
│       ├── case_manager.py
│       ├── evidence_coordinator.py
│       ├── ingestion.py
│       ├── reasoning_update.py
│       ├── intelligence_cycle.py
│       ├── action_router.py
│       ├── action_planning.py
│       ├── service_directory.py
│       ├── service_orchestrator.py
│       ├── service_transaction.py
│       ├── execution_gateway.py
│       ├── action_execution.py
│       ├── outcome_feedback.py
│       ├── experience_memory.py
│       └── experience_summary.py
│           # Decision execution, service routing and feedback
│
├── tests/
│   # Unit, integration-boundary and closed-loop regression tests
│
├── pyproject.toml
└── README.md
```

---

# 6. Detailed implemented features

## 6.1 Farm Digital Twin

The Farm Digital Twin is the persistent context layer.

Current domain objects include:

- `Farm`
- `Parcel`
- `GeoPoint`
- `CropCycle`
- `LandParty`
- `ProductionContract`
- `Observation`
- `FarmSnapshot`

### Supported context

- farm identity;
- parcel area;
- parcel geometry;
- administrative location;
- active crop;
- crop-cycle dates;
- land owner;
- cultivator;
- production relationship;
- time-bounded contracts;
- historical observations.

### Important design choice

Ownership and cultivation are represented separately.

This allows future workflows to distinguish:

```
Legal ownership
      ≠
Actual cultivation
      ≠
Seasonal production contract
```

That distinction is important for Indian agricultural service delivery, insurance, credit and benefit workflows.

---

# 7. Observation and evidence architecture

OFI represents observations using explicit types:

- farmer report;
- image;
- soil;
- weather;
- satellite;
- market;
- sensor;
- model;
- expert.

Observations can contain:

- timestamp;
- location;
- crop-cycle association;
- unit;
- spatial scope;
- quality;
- confidence;
- provenance;
- source/provider metadata.

The system therefore has a basis for answering:

> What was observed, when, where, by whom/provider, with what quality, and how reliable is it?

rather than only:

> What did the model say?

---

# 8. Provider architecture

The provider layer separates external evidence sources from intelligence logic.

Current provider concepts include:

```
EvidenceProvider
      │
      ├── Weather
      ├── Satellite
      └── Soil
```

A provider produces evidence which is normalized into OFI's canonical representation.

### Current capabilities

- provider registration;
- capability discovery;
- provider collection;
- provider-specific adapters;
- canonical payload validation;
- provenance preservation;
- provider failure isolation.

Mock providers are deliberately available so that the intelligence layer can be tested without external APIs.

---

# 9. Evidence normalization

Different providers describe the same phenomenon differently.

OFI therefore has a normalization layer for:

- weather observations;
- satellite observations;
- soil observations;
- provider/source identifiers;
- satellite scene metadata.

Satellite normalization can represent:

- NDVI;
- NDVI trend;
- EVI;
- NDWI;
- cloud cover;
- valid-pixel fraction;
- pixel count;
- mean/median vegetation indices.

The objective is to prevent downstream reasoning code from becoming provider-specific.

---

# 10. Evidence fusion and uncertainty

OFI does not currently treat evidence as equally trustworthy.

A transparent reliability layer provides source-level weights and freshness windows.

Example conceptual ordering:

```
expert / validated lab
        ↓
calibrated sensor
        ↓
satellite / station
        ↓
weather model
        ↓
farmer report
        ↓
generic model inference
```

These are **engineering heuristics**, not calibrated probabilities.

Evidence scores combine factors such as:

- source reliability;
- observation quality;
- stated confidence;
- freshness.

The system can also detect material disagreement between sources.

### Example

```
Satellite: vegetation declining
Soil: high moisture
Weather: recent rainfall
Model: low water stress
```

The correct behavior is not:

> "I am 100% sure the crop is water stressed."

Instead, OFI can recognize a conflict and request verification.

---

# 11. Spatial and temporal alignment

Evidence is only useful if it refers to the correct:

- place;
- time;
- unit;
- crop/field context.

Current alignment capabilities include:

- point-in-parcel validation;
- footprint overlap;
- temporal matching;
- comparable observation pairing;
- unit compatibility;
- parcel/raster alignment;
- screening-level raster aggregation.

Satellite evidence is explicitly checked against the actual farm parcel rather than blindly accepted because a provider returned a scene.

---

# 12. Geospatial analytics

Current screening-level analytics include:

- NDVI trend;
- vegetation stress index;
- crop age;
- recent satellite selection;
- parcel-level NDVI aggregation;
- raster/parcel overlap concepts.

The current implementation intentionally avoids claiming production-grade remote-sensing accuracy.

The future production path should move computationally heavy geospatial operations toward:

- PostGIS;
- raster databases;
- validated remote-sensing pipelines;
- field-specific calibration.

---

# 13. Scientific engine

OFI has a separate scientific model interface.

```
ScientificModel
      │
      ├── FAO-56-style water balance
      ├── future disease models
      ├── future crop models
      └── future validated regional models
```

The current water-balance implementation includes:

- ET0-style calculation;
- crop coefficient stages;
- root-zone water balance;
- crop-stage interpretation;
- soil/weather inputs.

Initial crop coefficient screening values exist for selected crops including:

- bajra;
- wheat;
- mustard.

### Safety boundary

The current scientific engine is a **screening model**.

It is not yet:

- Rajasthan calibrated;
- crop-variety calibrated;
- experimentally validated for local soil regimes;
- suitable by itself for professional agronomic prescription.

This distinction is intentional.

---

# 14. Hypothesis engine

The intelligence layer converts evidence into competing hypotheses.

Current hypothesis patterns include:

### Water stress

Potential supporting evidence:

- low soil moisture;
- low recent rainfall;
- declining NDVI;
- low NDWI;
- scientific-model stress.

Potential contradictory evidence:

- high soil moisture;
- recent rainfall;
- low model-estimated stress.

### Disease stress

Potential supporting evidence:

- image-based disease signs;
- adequate moisture;
- declining vegetation indices.

The architecture allows additional hypotheses to be added without rewriting the whole decision system.

---

# 15. Decision policy

Current decisions include:

- `ADVISE`
- `ASK_FARMER`
- `REQUEST_TEST`
- `ESCALATE_EXPERT`

The current policy is intentionally conservative.

Conceptually:

```
Strong evidence + sufficient margin
        → ADVISE

Moderate evidence
        → REQUEST_TEST

Conflicting evidence
        → REQUEST_TEST / verification

Weak or ambiguous evidence
        → ASK_FARMER / ESCALATE_EXPERT
```

The thresholds are engineering rules, not learned/calibrated probabilities.

---

# 16. Case lifecycle

A case represents an ongoing farm decision problem.

The lifecycle supports:

```
Create case
    ↓
Add evidence
    ↓
Reason
    ↓
Plan action
    ↓
Execute / route
    ↓
Record outcome
    ↓
Learn from experience
```

Cases have:

- durable event IDs;
- version information;
- reasoning state;
- outcome state;
- append-only event history.

The architecture also supports optimistic version checks to prevent silent overwrites in persistent storage.

---

# 17. Action planning

A decision is translated into an executable action.

Current action categories include examples such as:

- soil test;
- crop disease diagnosis;
- KVK referral;
- agricultural extension;
- evidence verification.

The action planner separates:

```
Reasoning
   ↓
Decision
   ↓
Action plan
   ↓
External execution
```

This is important because deciding that a soil test is required is not the same thing as actually ordering one.

---

# 18. Service directory

The service layer is provider-neutral.

A service provider can advertise:

- service;
- capability;
- supported action types;
- region;
- language;
- operational status;
- metadata.

OFI can discover providers for a required capability.

This provides a foundation for future integration with:

- KVKs;
- agricultural extension;
- diagnostic laboratories;
- FPOs;
- cooperatives;
- custom hiring centres;
- banks;
- insurance;
- input/service providers;
- VISTAAR/Beckn-style service networks.

---

# 19. Service selection

The implementation separates **eligibility** from **selection**:

1. `ServiceDirectory.discover()` filters providers by service, capability, action, geography and language.
2. `ProviderSelectionPolicy` ranks eligible matches deterministically.
3. `ServiceOrchestrator` returns the selected provider with an explainable rationale.

The current metadata-aware ranking considers geography fit, language fit, availability, health, trust score, SLA and distance, with provider ID as a stable final tie-breaker. These are transparent engineering heuristics: metadata quality and score calibration are not yet validated against live provider performance. Cost optimization is not implemented.

The policy should remain explainable and deterministic before introducing opaque AI ranking.

---

# 20. Service transactions

Service execution has its own lifecycle.

Current transaction states include:

```
planned
  ↓
submitted
  ↓
accepted
  ↓
in_progress
  ↓
completed
```

Failure paths include:

- rejected;
- failed;
- cancelled.

Terminal states cannot be arbitrarily transitioned.

This separates:

```
Action planning status
        from
External service execution status
```

A transaction can also be projected back into an action status.

---

# 21. Execution gateway

The execution gateway is the boundary between OFI and external services.

Current concepts include:

- actor identity;
- consent;
- provider identity;
- idempotency;
- service adapters;
- transaction creation;
- execution receipts;
- provider-aware status updates.

Repeated submissions with the same idempotency key are intended to avoid accidental duplicate execution.

The current mock adapter provides deterministic local execution for testing.

---

# 22. Consent and actor binding

External execution must not become an unrestricted function call.

The current execution boundary associates requests with:

- actor identity;
- consent;
- service/provider;
- action;
- idempotency.

Future production work must extend this toward:

- explicit consent scopes;
- purpose binding;
- expiry;
- revocation;
- provider/service-specific authorization;
- audit requirements.

---

# 23. Outcome feedback

After an action is executed, the system can record the result.

An outcome can produce a bounded learning signal:

```
positive → +1
neutral  →  0
negative → -1
unknown  →  0
```

Attribution confidence can weight the signal.

The system deliberately does **not** claim that an observed outcome proves causality.

For example:

> Yield improved after an irrigation-related action

does not automatically prove:

> The action caused the yield improvement.

This distinction is essential for responsible agricultural learning systems.

---

# 24. Experience memory

OFI maintains empirical experience memory based on contextual similarity.

A context can include:

- crop;
- season;
- irrigation;
- hypotheses;
- evidence;
- administrative region.

Experience memory is intended for:

- retrieval;
- comparison;
- evaluation;
- future decision support.

It is **not autonomous model training**.

This creates a path toward local adaptation without pretending that a small number of farm outcomes are sufficient for causal or statistical claims.

---

# 25. Persistence architecture

The repository boundaries are intentionally explicit.

Current persistence concepts:

### Farm Twin

```
FarmTwinRepository
      │
      ├── In-memory implementation
      └── PostgreSQL/PostGIS implementation
```

### Cases

```
CaseRepository
      │
      ├── In-memory implementation
      └── PostgreSQL implementation
```

The PostGIS schema includes tables for:

- farms;
- parcels;
- crop cycles;
- observations;
- land parties;
- production contracts;
- case records;
- case events.

Spatial data uses PostGIS geography types and spatial indexes.

---

# 26. Transactional integrity

The project treats state and events as related durable information.

The PostgreSQL case repository supports an atomic:

```
case state update
       +
case event append
```

operation.

Optimistic versioning prevents an outdated case snapshot from silently overwriting a newer one.

### Known next improvement

A complete production architecture still needs a clear unit-of-work boundary when one operation modifies both:

- Farm Digital Twin state; and
- Case state.

That cross-repository atomicity is therefore an explicit roadmap item.

---

# 27. API and application boundary

The repository includes a FastAPI application entry point.

Development startup:

```bash
uvicorn ofi.api:app --reload
```

The API boundary is intentionally kept separate from domain and service logic so the same core can eventually support:

- web applications;
- mobile applications;
- voice interfaces;
- agents;
- institutional dashboards;
- VISTAAR-style network integrations.

---

# 28. AI / LLM architecture

AI is deliberately **downstream of the evidence and science substrate**.

Potential AI responsibilities:

- multilingual interaction;
- voice understanding;
- retrieval;
- adaptive questioning;
- image interpretation;
- explanation;
- summarization;
- planning;
- translating technical evidence into farmer-friendly language.

AI should **not** independently become the authority for:

- soil facts;
- weather facts;
- scientific thresholds;
- crop diagnosis;
- government eligibility;
- transaction state.

The desired architecture is:

```
Trusted data + scientific models
              ↓
       OFI reasoning layer
              ↓
      AI interaction layer
              ↓
          farmer
```

not:

```
LLM
 ↓
invented agricultural answer
```

---

# 29. Relationship with Bharat-VISTAAR

OFI is designed as a complementary intelligence layer.

Conceptually:

```
Bharat-VISTAAR
      │
      │ digital agricultural network
      ▼
┌───────────────────────┐
│ Open Farm Intelligence│
│                       │
│ farm context          │
│ evidence fusion       │
│ reasoning             │
│ uncertainty           │
│ decision              │
│ action orchestration  │
│ feedback              │
└───────────┬───────────┘
            │
            ▼
     Farmer / Service
```

Potential integration domains include:

- VISTAAR/Beckn-style provider discovery;
- ICAR scientific information;
- KVK/expert escalation;
- IMD weather;
- AgMarkNet market data;
- Soil Health Card information;
- state agriculture systems;
- schemes;
- diagnostic services;
- FPO/cooperative services.

The integration should happen through adapters rather than contaminating the core domain with provider-specific assumptions.

---

# 30. Current development status

## Overall status

**Milestone: Reliability-hardened functional prototype / pre-production foundation**

The core closed-loop architecture has a tested implementation, and the execution layer has durable idempotency, callback receipts, explicit attempts and a safe retry assessment. The project is still **not production-ready** for agricultural deployment.

### Reliability progress — October 2026

- ✅ Atomic farm-case unit-of-work boundary
- ✅ Durable per-case event sequencing
- ✅ PostgreSQL/PostGIS integration coverage
- ✅ Deterministic provider-selection policy
- ✅ Durable service transactions
- ✅ Atomic idempotency claim preventing concurrent duplicate execution
- ✅ Provider external-reference persistence
- ✅ Authenticated provider callback boundary (HMAC-SHA256)
- ✅ Exactly-once callback receipt ledger
- ✅ Provider-to-transaction ownership validation
- ✅ Callback replay returns the transaction originally bound to the provider event
- ✅ Real PostgreSQL callback replay integration test
- ✅ Callback-to-attempt identity (callbacks must name the attempt they update)
- ✅ Signed-payload field binding to the parsed callback object
- ✅ Callback updates transaction and named attempt in one PostgreSQL transaction
- ✅ HMAC-authenticated reconciliation evidence bound to provider, transaction and attempt
- ✅ Reconciliation evidence timestamp freshness and clock-skew validation
- ✅ Reconciliation evidence signed-payload binding
- ✅ Append-only attempt-level audit ledger for creation and status transitions
- ✅ Atomic audit writes for dispatch claims, provider outcomes, callbacks and reconciliation
- ✅ Durable event ordering and read API for attempt audit history
- ✅ PostgreSQL trigger prevents audit-row updates/deletes
- ✅ In-memory and PostgreSQL regression tests for audit ordering, replay and immutability
- ✅ Concurrent duplicate callback stress tests for in-memory and PostgreSQL repositories
- ✅ Concurrent duplicate reconciliation stress tests for in-memory and PostgreSQL repositories
- ✅ Failure-injection test proves an audit append failure rolls back the associated PostgreSQL attempt transition

### Recovery operations runbook

A deployment-oriented runbook documents scheduler concurrency and timeout contracts, explicit health thresholds, structured-log alert routing, ambiguous-outcome safety, and pre-production checks: [`docs/dispatch-recovery-operations.md`](docs/dispatch-recovery-operations.md). The `ofi-recovery` command supports a concrete composition root, `ofi.services.deployment_recovery:build_worker`, which wires `PostgresTransactionRepository` and signed HTTPS reconciliation adapters from deployment environment variables. The factory now verifies PostgreSQL connectivity and required transaction/attempt/reconciliation tables at startup; this does not replace migrations or end-to-end tests. Provider endpoints implementing OFI's signed response contract, scheduler, alert receiver, credentials and provider authority remain deployment responsibilities.

### Current execution boundary

The callback path verifies HMAC-SHA256, binds typed fields to signed payloads, validates ownership and applies events to the named attempt. Reconciliation evidence is signed, freshness-checked, bound to the exact provider/transaction/attempt, durably deduplicated, and applied atomically. Attempt-level audit events now record creation, status transitions, provider callback application and reconciliation decisions. PostgreSQL writes the state transition and its trigger-generated audit event in the same transaction; the audit table rejects row updates/deletes. Provider-specific production adapters, secret rotation, production scheduler configuration, external idempotency guarantees and operational alerting remain open.

### Next reliability milestone

The concurrency and audit-failure milestone has in-memory and real-PostgreSQL regression coverage. The canonical correlation ID is the durable transaction ID; the provider request ID is the exact attempt ID. Both are exposed consistently on execution requests/receipts, callbacks, reconciliation evidence and audit events without duplicating stored identifiers. The dispatch recovery report exposes low-cardinality outcome/error counters, and `src/ofi/services/recovery_health.py` provides a deterministic assessment against explicit deployment thresholds for errors, unknown attempts and pending reconciliations. Unit tests cover healthy, warning, critical, pending and invalid-threshold behavior. The new `src/ofi/services/recovery_observability.py` adapter emits a JSON-serializable structured event through Python's standard logger, with UTC timestamp, health status, low-cardinality counters and alert codes. It excludes provider IDs, attempt/transaction IDs, raw provider payloads and exception text; tests cover serialization, timestamp validation, log level and sensitive-detail exclusion. `src/ofi/services/recovery_runtime.py` now composes one worker run, threshold assessment and structured log event into a scheduler-callable `run_recovery_cycle`; integration tests verify the shared timestamp, configured worker arguments, health level and safe event payload. This is a runtime entry point, not a scheduler or external alert receiver. Still open: connect this event to the chosen deployment's log/metrics/alert receiver; select and document real scheduler cadence, timeout/backoff and alert thresholds; validate real provider adapters and provider-supported external idempotency. No universal thresholds or deployment platform are assumed. Preserve the invariant that audit writes and state changes commit or roll back together.

```
Action
  └── Attempt
       ├── submitted
       ├── accepted / rejected
       ├── in_progress
       ├── completed
       └── failed / unknown
```

A provider timeout or lost callback must not be interpreted as confirmed external failure. Attempt-scoped reconciliation evidence is authenticated and applied atomically. The guarded retry orchestrator creates a prepared `ready` attempt only after applied, provider-bound `not_executed` evidence for the latest failed attempt. The execution gateway now claims a `ready` attempt atomically into a distinct `dispatching` state before provider dispatch, refuses a second local dispatch of the same attempt, and marks transport exceptions as `unknown`. A bounded stale-dispatch recovery operation changes abandoned `dispatching` attempts to `unknown`—never to failed or not-executed—so reconciliation is required before another attempt. A scheduler-invokable worker now finds these candidates, calls the provider-specific reconciliation adapter, and leaves unresolved outcomes eligible for a later pass. It never creates a retry or resends the external action. Production scheduling, provider idempotency, observability and deployment validation remain open. A deterministic health-assessment policy now converts each worker report into low-cardinality `healthy`, `warning` or `critical` status using explicitly configured per-deployment thresholds for reconciliation errors, unresolved unknown attempts and pending reconciliations. It is a pure assessment: it neither mutates execution state nor initiates retries, and it deliberately avoids exposing provider error text. Threshold values are required from deployment configuration rather than presented as universal defaults.

The core closed-loop architecture is implemented and tested, but the project is **not production-ready**.

### Status legend

- ✅ Implemented and tested
- 🟡 Implemented but requires hardening/validation
- 🔵 Designed / integration-ready
- ⬜ Not yet implemented
- ⚠️ Explicit safety/validation boundary

---

## 31. Development tracker

### A. Core domain

- [x] Farm domain model
- [x] Parcel geometry
- [x] Crop-cycle model
- [x] Owner/cultivator separation
- [x] Production contract model
- [x] Time-aware observations
- [x] Observation provenance
- [x] Farm snapshot
- [x] Case model
- [x] Evidence model
- [x] Hypothesis model
- [x] Decision model
- [x] Outcome model

**Status: ✅ Foundation complete**

---

### B. Farm Digital Twin

- [x] In-memory Farm Twin
- [x] Time-aware snapshot
- [x] Active crop-cycle selection
- [x] Active land-party selection
- [x] Active contract selection
- [x] Parcel location
- [x] Parcel boundary
- [x] Repository abstraction
- [x] PostGIS persistence adapter
- [x] PostGIS schema
- [ ] Production migration system
- [ ] Production backup/recovery strategy
- [x] Initial cross-tenant API and PostgreSQL persistence validation (PR #69)
- [ ] Tenant membership administration and audited legacy-data cutover
- [ ] Full PostGIS integration test suite

**Status: 🟡 Functional; production persistence remains**

---

### C. Evidence providers

- [x] Provider interface
- [x] Provider registry
- [x] Capability discovery
- [x] Weather adapter
- [x] Satellite adapter
- [x] Soil adapter
- [x] Canonical payload validation
- [x] Provenance retention
- [x] Provider failure isolation
- [x] Mock providers
- [ ] IMD integration
- [ ] Sentinel/Copernicus production integration
- [ ] Soil laboratory integration
- [ ] Sensor ingestion
- [ ] Farmer image ingestion
- [ ] Market provider integration
- [ ] Provider authentication/credential management

**Status: 🟡 Interface complete; real providers pending**

---

### D. Evidence intelligence

- [x] Source reliability
- [x] Evidence quality
- [x] Freshness scoring
- [x] Temporal alignment
- [x] Spatial alignment
- [x] Unit compatibility
- [x] Conflict detection
- [x] Evidence fusion
- [x] NDVI-derived evidence
- [ ] Calibrated reliability models
- [ ] Statistical uncertainty estimation
- [ ] Probabilistic evidence fusion
- [ ] Production raster processing
- [ ] Automated evidence provenance graph

**Status: 🟡 Screening-level intelligence**

---

### E. Scientific engine

- [x] Scientific model interface
- [x] Scientific model registry
- [x] ET0-style calculation
- [x] Crop coefficient stages
- [x] Root-zone water balance
- [x] Water-stress signal
- [x] Initial crop support
- [ ] Rajasthan calibration
- [ ] Variety-level calibration
- [ ] Soil-specific calibration
- [ ] Historical validation
- [ ] Field-trial validation
- [ ] Expert review protocol
- [ ] Model versioning
- [ ] Model performance monitoring

**Status: ⚠️ Screening model only**

---

### F. Reasoning and decision engine

- [x] Hypothesis generation
- [x] Water-stress hypothesis
- [x] Disease-stress hypothesis
- [x] Contradiction handling
- [x] Confidence scoring
- [x] Conservative decision policy
- [x] ASK_FARMER path
- [x] ADVISE path
- [x] REQUEST_TEST path
- [x] ESCALATE_EXPERT path
- [ ] Learned hypothesis ranking
- [ ] Calibrated confidence
- [ ] Causal reasoning
- [ ] Counterfactual analysis
- [ ] Multi-objective farm planning

**Status: 🟡 Functional prototype**

---

### G. Case management

- [x] Case creation
- [x] Evidence updates
- [x] Reasoning updates
- [x] Outcome recording
- [x] Escalation
- [x] Action planning
- [x] Durable event IDs
- [x] Append-only event history
- [x] Optimistic versioning
- [x] Atomic case state + event persistence
- [ ] Monotonic per-case event sequence
- [ ] Full concurrency test suite
- [ ] Event replay
- [ ] Event schema versioning

**Status: 🟡 Strong foundation; event infrastructure needs hardening**

---

### H. Action and service orchestration

- [x] Action request model
- [x] Action routing
- [x] Service capabilities
- [x] Service provider directory
- [x] Provider discovery
- [x] Service request
- [x] Service response
- [x] Service transaction
- [x] Explicit transaction state machine
- [x] Action-status projection
- [x] Provider identity preservation
- [x] Deterministic provider-selection policy
- [x] Geography-aware selection
- [x] Language-aware selection
- [x] Availability/health-aware selection
- [x] Trust-score ranking
- [x] SLA-aware routing
- [x] Distance-aware ranking
- [ ] Cost optimization
- [ ] Live-provider metadata calibration and monitoring

**Status: 🟡 Deterministic policy implemented; live metadata, calibration and cost optimization remain**

---

### I. External execution

- [x] Execution gateway
- [x] Service adapter boundary
- [x] Actor identity
- [x] Consent boundary
- [x] Idempotency
- [x] Execution receipt
- [x] Transaction lifecycle
- [x] Mock execution adapter
- [x] Signed provider callbacks
- [x] Callback authentication
- [x] Signed-payload field binding
- [x] Callback-to-attempt identity
- [x] Atomic transaction + attempt callback update
- [x] External event idempotency
- [x] Retry policy
- [ ] Dead-letter handling
- [x] Explicit execution-attempt ledger
- [x] Unknown external execution state
- [x] Guarded multi-attempt retry preparation after authoritative non-execution reconciliation
- [x] Authenticated attempt-scoped reconciliation evidence contract
- [x] HTTPS provider reconciliation adapter with signed response verification
- [x] Durable reconciliation receipt ledger with event-ID/payload binding
- [x] Atomic application of verified reconciliation evidence to the exact attempt
- [x] Retry eligibility assessment without automatic retry submission
- [x] Guarded retry attempt preparation after applied non-execution evidence
- [x] Idempotent retry request keys and retry-of attempt lineage
- [x] Atomic PostgreSQL attempt-number allocation under transaction lock
- [x] Concurrent duplicate retry request integration test
- [x] One-time atomic dispatch claim for prepared attempts
- [x] Gateway dispatch of prepared attempts through registered provider adapters
- [x] Separate `dispatching` state for claimed but not yet acknowledged attempts
- [x] Ambiguous dispatch exceptions transition attempt to unknown
- [x] Bounded stale-dispatch recovery transitions abandoned claims to unknown
- [x] Concurrent dispatch claim test for in-memory and PostgreSQL repositories
- [x] Stale-dispatch recovery tests for in-memory and PostgreSQL repositories
- [x] Scheduler-invokable worker for stale-claim recovery and provider reconciliation
- [x] Retry later when reconciliation is unavailable or remains ambiguous
- [x] Worker never creates retries or resubmits provider work
- [x] Append-only attempt audit events for creation and status transitions
- [x] Audit records for provider callback and reconciliation decisions
- [x] PostgreSQL audit writes share the state-change transaction
- [x] Audit-row mutation rejected by database trigger
- [x] Concurrent duplicate callback stress tests (in-memory and PostgreSQL)
- [x] Concurrent duplicate reconciliation stress tests (in-memory and PostgreSQL)
- [x] Failure-injection test for audit append rollback
- [x] Canonical correlation ID (transaction ID) and provider request ID (attempt ID) exposed end-to-end
- [x] Dispatch recovery report exports low-cardinality counters for outcomes and errors
- [x] Structured health event emitted through standard logging
- [x] Scheduler-callable recovery cycle combines worker, health policy and structured logging
- [ ] Deployment scheduler configuration and operational alerting
- [ ] Operational metrics and alerts
- [ ] Provider-supported external idempotency and real service adapter
- [ ] Production dispatch observability and secret management

**Status: 🟡 Guarded retry, one-time dispatch claims, stale-claim recovery, reconciliation worker, attempt-level audit history and scheduler-callable health logging are implemented; production scheduling, provider idempotency, external alerting and real integrations remain**

---

### J. Feedback and learning

- [x] Action outcome model
- [x] Learning signal
- [x] Attribution confidence
- [x] Experience memory
- [x] Context similarity
- [x] Empirical effectiveness summary
- [x] Explicit non-causal boundary
- [ ] Longitudinal farm learning
- [ ] Population-level evaluation
- [ ] Causal inference framework
- [ ] Model retraining pipeline
- [ ] Drift detection
- [ ] Outcome quality monitoring

**Status: 🟡 Empirical memory exists; learning system is not yet statistical/causal**

---

### K. AI interaction layer

- [ ] LLM integration
- [ ] Retrieval layer
- [ ] RAG/evidence grounding
- [ ] Hindi/regional-language interaction
- [ ] Voice input
- [ ] Voice output
- [ ] Image understanding
- [ ] Adaptive questioning
- [ ] Explanation generation
- [ ] Human-in-the-loop review
- [ ] AI evaluation suite
- [ ] Prompt/version management
- [ ] hallucination/grounding evaluation

**Status: 🔵 Deliberately downstream; do not add before core evidence interfaces are stable**

---

### L. VISTAAR / ecosystem integration

- [ ] VISTAAR service discovery adapter
- [ ] Beckn-compatible adapter
- [ ] Provider registration mapping
- [ ] Service request mapping
- [ ] Service response mapping
- [ ] ICAR evidence adapter
- [ ] KVK referral adapter
- [ ] IMD adapter
- [ ] AgMarkNet adapter
- [ ] State agriculture adapter
- [ ] Scheme-service adapter
- [ ] Production integration testing

**Status: 🔵 Architecture-ready; real integration pending**

---

### M. Governance and security

- [x] Actor identity boundary
- [x] Consent boundary
- [x] Provider identity
- [x] Provenance representation
- [x] Versioning
- [x] Explicit service scopes on consent grants
- [ ] Purpose limitation across all downstream data uses
- [x] Consent expiry and timezone-aware validity
- [x] Actor-bound revocation with durable PostgreSQL state
- [x] Authenticated consent lifecycle API (create, retrieve, revoke)
- [x] JWT-backed resource ownership checks for case/farm/evidence/assessment routes
- [ ] Encryption at rest
- [ ] Encryption in transit configuration
- [ ] Audit log
- [ ] Role-based access control
- [x] Cross-tenant resource/persistence regression tests (initial boundary)
- [ ] Tenant membership/administration model
- [ ] Audited legacy-tenant mapping and cutover
- [ ] Data retention policy
- [ ] Privacy/data-sharing policy
- [ ] Security audit

**Status: 🟡 Scoped consent, durable revocation, authenticated consent lifecycle, tenant-scoped core persistence, and cross-tenant regression tests are implemented. Tenant membership administration and legacy-data cutover, farmer-facing consent UX, full RBAC, downstream purpose enforcement, encryption, audit, and broader security review remain**

---

## Safe retry invariant

An external execution timeout is not proof of failure. OFI must not create another external attempt while the provider's execution state is unknown.

The retry policy therefore requires:

```
local attempt state
      ↓
provider reconciliation
      ↓
execution confirmed?
  ├── yes → do not retry
  └── no  → retry may be allowed
```

A local `failed`, `rejected`, or `unknown` state alone is insufficient to authorize a new external attempt. The repository now requires applied, provider-bound `not_executed` evidence for the latest failed attempt, a submitted parent transaction, and an idempotent retry request key before preparing the next attempt. The new attempt starts as `ready`; it has not been sent externally.

---

# 32. Engineering hardening backlog

These are high-priority engineering issues rather than new features.

## Priority 1 — Persistence consistency

### Problem

Farm Twin and Case Repository are separate persistence boundaries.

A workflow can theoretically update one successfully and fail on the other.

### Target

Introduce an explicit unit-of-work boundary for operations requiring atomic updates across:

```
Farm Twin + Case + Event
```

---

## Priority 2 — Case event ordering

Current event IDs are durable, but UUID ordering is not chronological.

### Target

Introduce a monotonic per-case event sequence:

```
case_id + sequence
```

while retaining globally unique event IDs.

---

## Priority 3 — CaseManager mutation semantics

Some mutations currently perform an atomic event/state operation followed by another save.

### Target

Refactor each mutation toward:

```
validate
   ↓
mutate state
   ↓
append event
   ↓
single atomic commit
```

This reduces unnecessary writes and makes persistence semantics easier to reason about.

---

## Completed priority — Provider selection policy

The deterministic `ProviderSelectionPolicy` is implemented and integrated with `ServiceOrchestrator`. It ranks already-eligible providers by context fit and operational metadata, explains the selection, and uses provider ID as a stable tie-breaker. Live metadata validation and cost optimization remain open.

---

## Priority 5 — Reconciliation-driven retry orchestration

The attempt ledger, unknown-state representation, pure retry assessment, authenticated evidence verifier and HTTPS provider reconciliation adapter exist. The adapter requests reconciliation for one exact transaction/attempt, validates the response schema, and delegates HMAC-SHA256, signed-field consistency, provider/transaction/attempt binding, freshness and bounded future-clock-skew checks to `verify_reconciliation_evidence()`. Transport is injectable for deterministic tests; the HTTPS transport uses a configured endpoint and `X-OFI-Signature` response header.

Implemented in the current reliability branch:

- guarded retry preparation after the exact latest attempt has applied, provider-bound `not_executed` evidence;
- durable retry-request idempotency and retry-of lineage on attempts;
- transaction-locked, monotonic PostgreSQL attempt-number allocation;
- duplicate-request concurrency tests for in-memory and real PostgreSQL repositories.

Still required:

- provider-specific endpoint compatibility and deployment-level secret management for the generic HTTPS adapter;
- deployment scheduler configuration, retry/backoff policy and operational alerting for the recovery worker;
- provider-supported external idempotency semantics and provider-specific endpoint compatibility;
- deployment-level secret management, observability and end-to-end provider tests proving status semantics.

The evidence verifier is a trust-boundary primitive, not proof that the provider's claim is truthful: that still depends on provider identity, key management and the authoritative source.

---

# 33. Testing strategy

Testing is organized around the architecture rather than only individual functions.

## Current test categories

- domain model validation;
- provider contracts;
- normalization;
- evidence fusion;
- freshness;
- spatial alignment;
- geospatial analytics;
- scientific model behavior;
- orchestrator decisions;
- case lifecycle;
- persistence behavior;
- service discovery;
- action routing;
- transaction state transitions;
- execution gateway;
- idempotency;
- feedback;
- experience memory;
- closed-loop workflows.

## Desired test pyramid

```
                 E2E / field workflows
                       ▲
                       │
               integration tests
                       ▲
                       │
               service boundary tests
                       ▲
                       │
                 unit tests
                       ▲
                       │
             domain invariants
```

Future tests should increasingly focus on:

- concurrency;
- failure recovery;
- provider timeouts;
- duplicate callbacks;
- stale evidence;
- contradictory evidence;
- invalid geospatial evidence;
- transaction retries;
- consent revocation;
- model version changes.

---

# 34. Definition of done

A feature is **not complete** merely because its Python code exists.

A production-oriented feature should eventually satisfy:

### Domain

- [ ] explicit domain contract;
- [ ] invariants documented;
- [ ] serialization/versioning considered.

### Evidence

- [ ] provenance;
- [ ] freshness;
- [ ] quality;
- [ ] spatial/temporal scope;
- [ ] failure behavior.

### Intelligence

- [ ] uncertainty behavior;
- [ ] contradiction behavior;
- [ ] explainable decision path;
- [ ] test coverage.

### Execution

- [ ] authorization;
- [ ] consent;
- [ ] idempotency;
- [ ] retry behavior;
- [ ] external status handling.

### Persistence

- [ ] transaction semantics;
- [ ] concurrency behavior;
- [ ] migration strategy;
- [ ] recovery behavior.

### Science

- [ ] source/model provenance;
- [ ] validation dataset;
- [ ] calibration;
- [ ] uncertainty;
- [ ] expert review.

### Production

- [ ] observability;
- [ ] security;
- [ ] privacy;
- [ ] governance;
- [ ] operational runbook.

---

# 35. Development roadmap

## Phase 0 — Architecture foundation

**Status: ✅ Completed**

- domain model;
- Farm Digital Twin;
- evidence abstraction;
- scientific model boundary;
- reasoning engine;
- action layer;
- service directory;
- transaction model;
- execution gateway;
- feedback loop;
- tests/CI.

---

## Phase 1 — Reliability hardening

**Status: 🟡 Current**

Focus:

1. multi-attempt retry orchestration and unknown external state;
2. retry/idempotency policy across provider attempts;
3. callback freshness, payload binding and secret rotation;
4. consent scopes and revocation;
5. stronger concurrency/failure-recovery tests;
6. production migration and observability hardening.

**Principle:**

> Do not add more intelligence until the existing intelligence has reliable state and execution semantics.

---

## Phase 2 — Real agricultural evidence

**Target**

Connect real sources behind existing interfaces:

- weather;
- satellite;
- soil;
- market;
- field observations;
- diagnostic services.

Then test the complete evidence pipeline using real historical cases.

---

## Phase 3 — Rajasthan scientific validation

Build a validation program around:

- Rajasthan agro-climatic zones;
- soil classes;
- irrigation regimes;
- major crops;
- crop varieties;
- local weather;
- field observations;
- KVK/agricultural university expertise.

The objective is not simply:

> "Does the formula run?"

It is:

> "Does the system make materially better decisions for the agricultural conditions in which it will actually operate?"

---

## Phase 4 — VISTAAR/service integration

Implement provider/service adapters without changing the intelligence core.

Target:

```
OFI decision
    ↓
service capability
    ↓
provider discovery
    ↓
VISTAAR/Beckn-style network
    ↓
real service
    ↓
execution status
    ↓
outcome
```

---

## Phase 5 — AI interface

Only after the evidence and service substrate is stable:

- multilingual AI;
- voice;
- image understanding;
- adaptive questioning;
- farmer-facing explanation;
- agentic planning.

The AI layer should consume structured evidence and return grounded explanations/actions.

---

## Phase 6 — Field pilot

Start with a constrained problem rather than "all agriculture".

For example:

```
Crop
+
Region
+
Problem
+
Evidence sources
+
Service pathway
+
Outcome metric
```

A pilot should have measurable baselines.

Possible evaluation dimensions:

- diagnostic accuracy;
- unnecessary service requests;
- time-to-action;
- farmer effort;
- expert escalation rate;
- outcome quality;
- false confidence;
- evidence freshness;
- service completion rate.

---

# 36. Suggested first real pilot

A realistic first pilot should be narrow enough to validate the architecture.

Example:

**Rajasthan semi-arid crop water-stress decision loop**

```
Farm parcel
    +
crop cycle
    +
weather
    +
soil moisture
    +
satellite vegetation indices
    ↓
water-stress hypothesis
    ↓
confidence / conflict
    ↓
farmer question OR soil/water verification
    ↓
expert/service
    ↓
field action
    ↓
follow-up observation
    ↓
outcome
```

This would test almost the entire OFI architecture without requiring every agricultural service on day one.

---

# 37. Research directions

Once the foundation is stable, the project can evolve toward research questions such as:

### Multimodal farm state estimation

How can:

```
satellite + weather + soil + image + farmer observation
```

be fused into a reliable field state?

### Decision-making under uncertainty

How should an agricultural system choose between:

```
act
ask
measure
wait
escalate
```

when information is incomplete?

### Active information acquisition

What is the **next observation worth collecting**?

This changes the system from passive prediction to:

> prediction + information acquisition.

### Local adaptation

How can empirical farm outcomes improve recommendations without confusing correlation with causation?

### Institutional intelligence

How can the system select not just an answer, but the right:

- person;
- institution;
- service;
- diagnostic;
- financial instrument;
- market pathway?

---

# 38. Architectural principles to preserve

These should be treated as project invariants.

### 1. Evidence before language

Never let generated language become the primary evidence layer.

### 2. Context before query

A farm is a temporal system, not a collection of independent questions.

### 3. Uncertainty before confidence theater

The system should be able to say:

> "We do not know yet."

### 4. Action before conversation completion

The objective is not to end a chat. It is to improve the farm decision.

### 5. Human escalation is a feature

Expert escalation is not a system failure.

### 6. Outcomes matter more than messages

The valuable memory is:

```
decision → action → outcome
```

not merely:

```
user → message → response
```

### 7. Open interfaces

No single provider should become the architecture.

### 8. Science is versioned

Scientific models and thresholds must have provenance and validation status.

### 9. Never silently upgrade heuristics into truth

A screening score remains a screening score until validated.

### 10. Build infrastructure before intelligence theater

A smaller reliable system is more valuable than a larger demo with weak state, evidence and execution semantics.

---

# 39. Current limitations

The following are explicitly known:

1. Real agricultural providers are not yet connected end-to-end.
2. Scientific models are screening-level.
3. Rajasthan-local calibration has not been completed.
4. The current provider-selection policy is intentionally simple.
5. Cross-repository atomicity remains to be implemented.
6. Production-grade event sequencing is implemented at the service-transaction boundary; broader event-stream replay/schema versioning remains.
7. External callbacks are authenticated and exactly-once at the receipt boundary, while execution attempts now preserve unknown external state; complete production webhook hardening remains.
8. Consent is foundational rather than complete governance.
9. AI/LLM interaction is not yet the primary interface.
10. No field deployment should be inferred from the existence of the prototype.

These are not hidden defects; they are tracked engineering boundaries.

---

# 40. Quick start

Requires **Python 3.11+**.

```bash
git clone https://github.com/vkraj1123/Open-farm-intelligence.git
cd Open-farm-intelligence

python -m pip install -e ".[dev]"

pytest -q
```

Run the development API:

```bash
uvicorn ofi.api:app --reload
```

Optional PostgreSQL support:

```bash
python -m pip install -e ".[postgres]"
```

Enable PostgreSQL with PostGIS and apply:

```
src/ofi/twin/schema.sql
```

---

# 41. Development philosophy

OFI is being built incrementally.

The preferred development order is:

```
Architecture
    ↓
Domain invariants
    ↓
Interfaces
    ↓
Deterministic implementation
    ↓
Tests
    ↓
Persistence
    ↓
Real data
    ↓
Scientific validation
    ↓
External services
    ↓
AI interface
    ↓
Field deployment
```

This deliberately reverses the common order of:

```
LLM demo
    ↓
more prompts
    ↓
more features
    ↓
production problems
```

---

# 42. Project maturity statement

**Current maturity:**

> **Architecture-complete, functionally demonstrable, reliability-hardened prototype with unvalidated agricultural science.**

The repository contains a tested implementation of the core decision-loop architecture.

It should **not** yet be described as:

- production agricultural advisory infrastructure;
- a validated agronomic decision system;
- a deployed farmer service;
- a replacement for agricultural experts;
- a production VISTAAR integration.

The next milestone is not feature volume.

The next milestone is **reliability + real evidence + scientific validation + real service execution**.

---

# 43. License

See the repository license file for the current licensing terms.

---

## Project principle

> **The goal is not to build an AI that talks about farming.**
>
> **The goal is to build an open intelligence layer that can continuously understand a farm, reason from evidence, act through the agricultural ecosystem, observe outcomes, and improve decisions responsibly.**


---

# 44. Recovery integration verification tracker

This tracker distinguishes deterministic unit coverage from verification against a real database. Percentages below are **engineering estimates for the recovery/deployment milestone**, not a claim that the whole agricultural product is that percentage complete.

| Recovery milestone | Estimated completion | Evidence / remaining work |
|---|---:|---|
| Execution-attempt lifecycle and retry safety | 100% of current scoped implementation | Unit and concurrency regression coverage; external provider guarantees still require contract validation |
| Recovery worker, health assessment, observability and CLI | 100% of current scoped implementation | Implemented; deployment-owned scheduling and alert delivery remain |
| PostgreSQL factory wiring and fail-fast readiness | 100% of repository implementation | Factory and startup checks are present; real PostgreSQL integration is verified in CI |
| Real PostgreSQL recovery integration | 100% of CI scope | Checked-in schema, factory, repository persistence and worker cycle passed in PostgreSQL CI; real staging remains pending |
| Real provider reconciliation contract | 50% harness-ready | Read-only staging contract harness now supports fail-closed configuration in dedicated staging jobs; real provider execution remains pending |
| Staging operations | 30% starter-template readiness | Runbook, runner and Kubernetes CronJob starter template exist; image publication, real provider configuration, staging deployment, alerts, operational owner and incident drills remain |
| Agricultural evidence and scientific validation | Not yet meaningfully measurable | Real evidence pipelines, Rajasthan calibration and field outcomes are still needed |

## Current verification status

The PostgreSQL integration test applies src/ofi/twin/schema.sql to the dedicated CI database, builds the actual PostgreSQL/HTTPS recovery factory, creates and reads a uniquely identified transaction and attempt through PostgresTransactionRepository, and runs one bounded worker cycle without contacting the placeholder provider endpoint. Local runs without OFI_POSTGRES_DSN skip this integration test explicitly; they do not count as real-database verification.

**Acceptance gate passed:** standard CI and PostgreSQL CI passed for the merged integration change. This verifies the repository's CI PostgreSQL integration, not a staging test against a real provider or provider-side idempotency.


### Opt-in real-provider contract check

The test `tests/test_live_provider_reconciliation_contract.py` can verify a real staging endpoint's signed response contract. It is skipped by default in normal CI; set `OFI_STAGING_REQUIRED=1` in a dedicated staging job to make missing configuration fail rather than silently skip. To run it, supply staging-only credentials via environment variables and four provider-issued read-only reconciliation cases covering `executed`, `not_executed`, `pending`, and `unknown`:

- `OFI_STAGING_PROVIDER_ID`
- `OFI_STAGING_RECONCILIATION_ENDPOINT` (absolute HTTPS URL)
- `OFI_STAGING_RECONCILIATION_SECRET` (secret manager; never commit or print)
- `OFI_STAGING_RECONCILIATION_CASES` (JSON array of objects with `transaction_id`, `attempt_id`, `expected_status`)

Run only against a provider-approved staging endpoint and provider-issued test attempts:

```bash
pytest -q tests/test_live_provider_reconciliation_contract.py
```

Inject the four required values from a secret manager, and set `OFI_STAGING_REQUIRED=1` in a dedicated staging job to enforce fail-closed behavior. Never commit or print the secret.

This check performs reconciliation GET requests only; it does not dispatch actions or mutate OFI transaction state. It verifies the configured provider's HMAC signature, payload schema, exact provider/transaction/attempt identity, timestamp freshness, digest shape, and expected status. If staging cannot supply authoritative examples for all four states, the full matrix remains unverified. Passing the harness does not by itself validate provider-side idempotency, scheduler operation, or alert delivery.


### Kubernetes recovery deployment starter

`deploy/kubernetes/ofi-recovery-cronjob.yaml` is a **template**, not a production-ready manifest. It demonstrates a bounded Kubernetes CronJob with overlap prevention, resource limits and Secret references. Replace the image and provider placeholders, create the referenced Secret through secret management, review cadence/thresholds, and validate in staging before applying to a live cluster. See section 9 of `docs/dispatch-recovery-operations.md`.


---

# 45. Farm evidence alignment milestone

The deterministic evidence-alignment service is implemented in `src/ofi/services/evidence_alignment.py`, with tests in `tests/test_evidence_alignment.py`. The request-scoped API is exposed at `POST /farms/{farm_id}/evidence-frame` and covered by `tests/test_evidence_frame_api.py`; the contract is documented in `docs/evidence-alignment.md`.

| Evidence-pipeline capability | Status | Boundary |
|---|---|---|
| Freshness classification | Implemented in scoped service | Requires caller-supplied positive freshness windows; missing policy excludes evidence |
| Active crop-cycle alignment | Implemented in scoped service | Explicitly crop-cycle-bound observations from another cycle are excluded |
| Duplicate-ID protection | Implemented in scoped service | Duplicate observation IDs are all excluded from the frame |
| Provenance visibility | Implemented in scoped service | Missing provenance is reported; strict exclusion is configurable |
| Preserve uncertainty inputs | Implemented in scoped service | Quality and confidence remain separate and are not treated as calibrated probabilities |
| Request-scoped evidence-frame API | Implemented | `POST /farms/{farm_id}/evidence-frame`; caller supplies explicit freshness policy; no persistence or automatic reasoning filter |
| Explicit pairwise evidence comparison | Implemented as opt-in rule-based screening | Caller specifies metric key, exact unit and spatial scope, time limit and absolute tolerance; result is not agronomic truth |
| Agronomic validation | Not measured | Requires authoritative sources, reviewed policies and field validation |

Freshness windows in documentation examples are illustrative, not production defaults. This service does not make recommendations or claim scientific validity. Standard and PostgreSQL CI must pass before the change is merged; that verifies code and regression behavior, not agricultural correctness.



# 46. Evidence-gated reasoning milestone

The evidence alignment service now has an opt-in path into the existing case reasoning flow. `POST /cases/{case_id}/reason-with-evidence-policy` requires caller-defined freshness windows and can require provenance; it returns both the case reasoning result and the exact eligibility frame used to filter the observations. The existing `POST /cases/{case_id}/reason` behavior remains unchanged.

| Capability | Status | Boundary |
|---|---|---|
| Explicit-policy evidence-gated reasoning | Implemented in this change | Caller must supply positive freshness windows; no hidden defaults |
| Traceability of reasoning input selection | Implemented in response | Frame lists eligible IDs, assessment issues and exclusion counts |
| Original case observation history | Preserved | Filtering applies to a deep copy used for reasoning, not stored history |
| Policy persistence/versioning | Not implemented | Policy remains request-scoped |
| Contradiction analysis across providers | Not implemented by this change | Requires metric/unit/scope/time comparability and reviewed tolerances |
| Agronomic or field validation | Not completed | CI verifies software behavior only |

This milestone connects data-quality assessment to reasoning without changing the default endpoint or asserting that eligible evidence is scientifically true. PR [#45](https://github.com/vkraj1123/Open-farm-intelligence/pull/45) passed standard CI (run 604) and PostgreSQL CI (run 739) before merge. It was merged to `main` as `239dc81c3a5613e939f9e6dea098afaa68a40ee2`. No staging deployment or field validation is implied.




# 47. Explicit evidence comparability milestone

An opt-in deterministic comparison service is available in `src/ofi/services/evidence_comparison.py`, with unit coverage in `tests/test_evidence_comparison.py`. The request-scoped API `POST /farms/{farm_id}/evidence-comparisons` returns both the existing evidence-alignment assessment and comparison outcomes. It only labels a pair `agree` or `disagree` when both observations are eligible and caller-defined metric, unit, spatial-scope, time and crop-cycle/source gates pass.

| Capability | Status | Boundary |
|---|---|---|
| Explicit comparison-rule schema | Implemented | No universal units, tolerances or time windows are invented |
| Pairwise assessment | Implemented | `agree`, `disagree` or `not_comparable` with reason codes |
| Eligibility gating | Implemented | Stale, future, unconfigured or otherwise excluded observations cannot drive a disagreement |
| API exposure and synthetic tests | CI verified | Standard run #622 and PostgreSQL run #757 passed before PR #47 merged |
| Rule persistence and immutable version history | Implemented in this milestone | PostgreSQL-backed when OFI_DATABASE_URL is configured; process-local memory remains the default |
| Scientific significance / source authority | Not inferred | A discrepancy does not establish which source is right or a crop diagnosis |
| Rajasthan field validation | Not completed | Requires authoritative data, expert review and measured field outcomes |

Comparison thresholds in examples are illustrative, not production recommendations. The service does not convert units or treat every numeric mismatch as a contradiction. These checks are deterministic software screening, not validated agricultural science.


**Verification:** PR [#47](https://github.com/vkraj1123/Open-farm-intelligence/pull/47) passed standard CI (run #622) and PostgreSQL CI (run #757) before merge. The feature was merged to `main` as [`f843aef402a43a9e2cf3f1d5531fbc6504a18579`](https://github.com/vkraj1123/Open-farm-intelligence/commit/f843aef402a43a9e2cf3f1d5531fbc6504a18579). These checks verify software behavior, not source authority, agronomic validity or field performance.



# 48. Versioned evidence comparison ledger

The policy-and-assessment ledger is implemented in src/ofi/services/evidence_comparison_ledger.py. Policies are immutable version records; publishing the same policy_id creates a new version. Each persisted assessment pins the exact policy version and stores the observation snapshot, eligibility frame, issue/exclusion reasons and comparison outputs. PostgreSQL schema additions are in src/ofi/twin/schema.sql, with append-only triggers for policy versions and assessment records.

| Capability | Status | Boundary |
|---|---|---|
| Policy version creation and retrieval | Implemented | New policy versions are append-only; version selection is explicit or latest-on-request |
| Historical assessment persistence | Implemented | Exact input observations, eligibility frame, comparison outcomes and selected version are recorded |
| PostgreSQL serialization | Implemented | Policy head row is locked while allocating next version; assessments reference policy/version by foreign key |
| API endpoints | Implemented | Create/get policies, create assessment from a selected policy, retrieve assessment by ID |
| In-memory reference ledger | Implemented | Process-local only; not durable across restarts |
| API PostgreSQL selection | Implemented | Configure OFI_DATABASE_URL, install the postgres extra and apply the schema before use |
| Durable farm/case state in the default API | Not completed by this change | The ledger stores durable policy/results, but the default case manager and farm twin remain in-memory unless configured separately |
| Scientific validation and policy governance | Not completed | Persisted reproducibility does not validate thresholds, source authority or agronomic conclusions |

This change is software infrastructure. CI success, when confirmed on the PR, demonstrates repository tests and PostgreSQL schema/repository behavior only; it does not constitute a deployment or field validation.


**Verification:** PR [#49](https://github.com/vkraj1123/Open-farm-intelligence/pull/49) passed standard CI (run #770) and PostgreSQL CI (run #635) before merge. The ledger/schema/API changes were merged to `main` as [`244240b0aee0e2d8fe2944fb7088e130aff52711`](https://github.com/vkraj1123/Open-farm-intelligence/commit/244240b0aee0e2d8fe2944fb7088e130aff52711). This verifies deterministic persistence and audit behavior in CI, not deployment, provider authority or agricultural field validation.


# 49. Durable API case and farm-twin persistence

The API now uses one configuration switch for PostgreSQL-backed case/farm state and the versioned evidence-comparison ledger. When `OFI_DATABASE_URL` is unset, the API retains the in-memory reference implementation for local development and tests. When set, `case_manager` is constructed with `PostgresFarmCaseUnitOfWork`, which shares one connection factory across `PostgresCaseRepository` and `PostGISFarmTwinStore`.

| Capability | Status | Boundary |
|---|---|---|
| API persistence selection | Implemented | `OFI_DATABASE_URL` opts into PostgreSQL; unset preserves in-memory behavior |
| Case and farm repository coordination | Implemented | The unit of work binds one connection and transaction for coordinated mutations |
| Case state and audit events | PostgreSQL-backed when configured | Uses `case_records` and `case_events` from the shared schema |
| Farm, crop-cycle and observation state | PostgreSQL/PostGIS-backed when configured | Requires the schema and PostGIS extension to be applied |
| Atomic case/farm mutations | Covered by PostgreSQL integration tests | Verifies persisted state survives manager recreation and a farm-write failure rolls back the case/farm transaction |
| Dependency configuration | Fail-closed | A configured DSN without `psycopg` causes an actionable startup error; no silent fallback |
| Comparison-ledger transaction coupling | Not automatic | The ledger uses the same DSN but assessment writes are separate from case/farm operations unless explicitly coordinated |
| Deployment and recovery readiness | Not demonstrated | Requires deployment configuration, backups/recovery drills and operational monitoring |
| Agronomic and field validation | Not completed | Software persistence tests do not validate agricultural conclusions |

**Verification:** PR [#51](https://github.com/vkraj1123/Open-farm-intelligence/pull/51) passed standard CI (run #774) and PostgreSQL CI (run #639) before merge. The implementation was merged to `main` as [`5f9ee680dcedc7a885c7c2691233a1876c5ab321`](https://github.com/vkraj1123/Open-farm-intelligence/commit/5f9ee680dcedc7a885c7c2691233a1876c5ab321). PostgreSQL integration tests use the dedicated `OFI_POSTGRES_DSN` test database. These checks verify software persistence, restart retrieval and rollback behavior, not live deployment, operational recovery readiness or agricultural validity.


# 50. Ordered schema migrations and persistence readiness

PR #53 added the version marker and `/ready` endpoint. PR [#55](https://github.com/vkraj1123/Open-farm-intelligence/pull/55) added ordered, checksummed SQL migrations and merged after standard CI run #784 and PostgreSQL CI run #649 passed. The application schema version is now **2**. Version 1 establishes the initial PostgreSQL/PostGIS farm, case, service and evidence schema. Version 2 adds a non-negative constraint to optimistic case-record versions. The migration implementation was merged to `main` as [`4d4f27685275344c8bd0a055caefee80fd7c10db`](https://github.com/vkraj1123/Open-farm-intelligence/commit/4d4f27685275344c8bd0a055caefee80fd7c10db).

`ofi-schema` applies pending files in `src/ofi/twin/migrations/` in numeric order, records each migration name and SHA-256 checksum, serializes concurrent migration runs with an advisory transaction lock, and rejects missing predecessors, checksum drift or a database version newer than the application. Existing v1 databases are adopted safely: the runner adds checksum metadata, backfills the known v1 fingerprint for the legacy marker, then applies v2. Applied SQL files are immutable; corrections must be new migrations.

The `src/ofi/twin/schema.sql` remains a compatibility bootstrap script for the version-1 baseline. Use `ofi-schema` for installations and upgrades. `/health` is liveness-only; `/ready` returns HTTP 503 if configured PostgreSQL persistence lacks connectivity, PostGIS, required relations or the current schema version. In-memory mode is explicitly marked non-durable.

| Capability | Status | Boundary |
|---|---|---|
| Ordered migration registry | Implemented in this PR | Contiguous version numbers, unique names and filenames; current version derives from the last registry entry |
| Immutable migration checksums | Implemented in this PR | Detects edits to SQL already marked as applied |
| Legacy v1 to v2 upgrade | Integration-tested in this PR | Validates pending migration application and repeatability against PostgreSQL CI |
| Concurrent migration guard | Implemented in this PR | PostgreSQL advisory transaction lock serializes migration attempts |
| Migration packaging | Implemented in this PR | SQL files are included in package distributions |
| Readiness and liveness separation | Previously merged in PR #53 | `/health` does not require DB; `/ready` checks persisted schema version |
| Production migration rehearsal and recovery | Not demonstrated | Requires staging, real backup/restore drills, and operational monitoring |
| Agricultural field validation | Not completed | Schema integrity is infrastructure quality, not agricultural model validation |

The ordered migration strategy and commands are described in `docs/database-operations.md`. **Verification:** PR [#55](https://github.com/vkraj1123/Open-farm-intelligence/pull/55) passed standard CI (run #784) and PostgreSQL CI (run #649) before merge. These checks validate migration behavior in CI, not live staging operations, production backup/recovery readiness, or agricultural field validity.

# 51. Staging migration and recovery rehearsal

PR [#57](https://github.com/vkraj1123/Open-farm-intelligence/pull/57) added `ofi-recovery-check`, a non-destructive source-to-disposable-target rehearsal for the currently registered v1→v2 upgrade. It passed the standard test workflow (run #657) and PostgreSQL-backed workflow (run #792) before merge as [`8af7eca25d2eaa2038dfb1a79c98dc058c527d07`](https://github.com/vkraj1123/Open-farm-intelligence/commit/8af7eca25d2eaa2038dfb1a79c98dc058c527d07). It takes a custom-format `pg_dump` from a source database, restores it into a separately provisioned empty database, applies pending migrations to that restored copy, and checks application readiness. The source database is not migrated or written by the harness.

The rehearsal compares row counts for every required persistence table and full-row fingerprints for core farm/case/service/evidence tables across the source snapshot, restored v1 copy, and upgraded v2 copy. A separate PostgreSQL integration test injects a negative case-record version to force v2 to fail and verifies that the DDL, checksum backfill, and migration marker all roll back; the repair and retry then succeeds.

| Capability | Status | Boundary |
|---|---|---|
| Disposable-target backup and restore | Implemented in this PR | Requires `pg_dump`, `pg_restore`, a prior-version source DB, and a distinct empty restore DB |
| Source non-mutation | Covered by integration test | Checks source schema stays on its original version after rehearsal |
| Data preservation | Covered by integration test | Row counts across required tables; content fingerprints across core tables; fixture checks on case/event/farm/crop/observation data |
| Migration failure rollback | Covered by PostgreSQL integration test | Intentionally invalid pre-existing data causes v2 constraint creation to fail; verifies transactional rollback then successful retry |
| CLI | Implemented in this PR | `ofi-recovery-check` uses `OFI_RECOVERY_SOURCE_DSN` and `OFI_RECOVERY_RESTORE_DSN`; restore target must be empty |
| Production recovery readiness | Not demonstrated | This CI/staging harness does not verify backup retention, off-site copies, point-in-time recovery, RTO/RPO, or a production incident recovery |

Operational commands and safety boundaries are documented in `docs/database-operations.md`. The CI tests use disposable databases on the isolated PostgreSQL service and install client utilities as part of the workflow. **Verification:** standard run [#657](https://github.com/vkraj1123/Open-farm-intelligence/actions/runs/38098149219) and PostgreSQL-backed run [#792](https://github.com/vkraj1123/Open-farm-intelligence/actions/runs/38098149214) passed on the PR head. This verifies a disposable CI backup/restore and migration rehearsal, not a production recovery event.


# 52. Encrypted scheduled staging recovery drills

PR [#59](https://github.com/vkraj1123/Open-farm-intelligence/pull/59) added the repeatable encrypted recovery drill and merged to `main` as [`b25b0b27132477db159c286665ce7b71216dbc1d`](https://github.com/vkraj1123/Open-farm-intelligence/commit/b25b0b27132477db159c286665ce7b71216dbc1d). The drill supports a source database at either the immediately prior schema version (to rehearse the next migration) or the current schema version (for recurring restoration verification).

The new `ofi-recovery-drill` command creates a uniquely named, empty restore database from a separate maintenance DSN, calls the recovery harness, and drops the temporary database after the check. The source database is read and dumped but never migrated or written by the rehearsal. The harness checks row counts for all required persistence relations, full-row fingerprints for core data relations, schema version, and `/ready` readiness after any pending migration.

The custom-format `pg_dump` is encrypted with `age` to the configured public recipient before the temporary plaintext dump is deleted. GitHub Actions retains the encrypted `.dump.age` artifact and a JSON report for **30 days**. The report records the encrypted artifact's byte size and SHA-256 checksum. A temporary age keypair in PostgreSQL CI verifies that the retained artifact decrypts back to a valid PostgreSQL custom-format dump; the same integration test confirms the ephemeral restore database is removed.

| Capability | Status | Boundary |
|---|---|---|
| Scheduled/manual workflow | Implemented in PR #59 | Runs Mondays at 09:00 Asia/Kolkata or via `workflow_dispatch`; it skips when required Actions secrets are absent |
| Current-schema restore rehearsal | Integration-tested in PostgreSQL CI | Verifies backup/restore without needing a pending migration; does not prove an actual live staging recovery |
| Encrypted backup artifact | Implemented and integration-tested | Uses an `age` public recipient; the private decryption identity must be stored separately and securely |
| Disposable restore database | Covered by integration test | A dedicated maintenance role needs database create/drop privileges; use staging-only infrastructure |
| Artifact retention | Implemented | GitHub Actions artifact retention is 30 days; this is not an independent off-site backup service |
| Production recovery readiness | Not demonstrated | Still needs approved off-site/provider-native backups, independent key custody, retention policy, real restore drills, and agreed RPO/RTO targets |
| Agricultural field validation | Not completed | Recovery infrastructure tests do not validate farm recommendations or agronomic outcomes |

**Verification:** PR CI passed the standard workflow ([run #676](https://github.com/vkraj1123/Open-farm-intelligence/actions/runs/38098779417)) and PostgreSQL-backed workflow ([run #811](https://github.com/vkraj1123/Open-farm-intelligence/actions/runs/38098779415)). Both workflows also passed on `main` after merge: [standard run #677](https://github.com/vkraj1123/Open-farm-intelligence/actions/runs/38098846894) and [PostgreSQL-backed run #812](https://github.com/vkraj1123/Open-farm-intelligence/actions/runs/38098846920). These runs validate repository tests only; the scheduled live workflow will remain skipped until the three required Actions secrets are configured.


# 53. Signed recovery health alert delivery

PR [#61](https://github.com/vkraj1123/Open-farm-intelligence/pull/61) introduced an optional HTTPS webhook for the dispatch-recovery runner and merged to `main` as [`b3caa9aa0630f8dbdaa5f7bcb41f97b9ecbefc51`](https://github.com/vkraj1123/Open-farm-intelligence/commit/b3caa9aa0630f8dbdaa5f7bcb41f97b9ecbefc51). The runner continues to emit structured JSON logs. When an optional alert receiver is configured, it sends warning/critical health events to that receiver; healthy events remain log-only.

The webhook sends a minimized allow-listed payload and signs the exact request body with HMAC-SHA256. A SHA-256 event ID supports receiver-side deduplication. The sender requires HTTPS, rejects URL credentials/query strings/fragments, does not follow redirects, uses a bounded timeout, treats non-2xx statuses as failures, and does not retry ambiguous network errors. Payloads omit provider IDs, transaction IDs, attempt IDs, raw provider response bodies, and exception text. Configured delivery failures produce a sanitized runner failure (exit code 2); successful delivery emits a JSON receipt.

| Capability | Status | Boundary |
|---|---|---|
| Optional signed alert delivery | Implemented in PR #61 | Deployment-owned receiver must verify HMAC and operate deduplication |
| Secret and endpoint validation | Implemented and unit-tested | Uses `OFI_RECOVERY_ALERT_ENDPOINT`, `OFI_RECOVERY_ALERT_SECRET`, optional `OFI_RECOVERY_ALERT_TIMEOUT_SECONDS`; no vendor-specific integration is bundled |
| Privacy-safe alert payload | Covered by tests | Only allow-listed aggregate counters/alert codes are sent; per-attempt troubleshooting remains in access-controlled audit records |
| Failure handling | Covered by tests | Non-2xx/transport errors fail closed with sanitized logs; ambiguous delivery is not retried automatically |
| End-to-end delivery | Not demonstrated | A real receiver, secret rotation, on-call routing, and synthetic delivery test remain deployment responsibilities |

**Verification:** PR #61 passed standard CI ([run #691](https://github.com/vkraj1123/Open-farm-intelligence/actions/runs/38099439423)) and PostgreSQL-backed CI ([run #826](https://github.com/vkraj1123/Open-farm-intelligence/actions/runs/38099439426)). These checks verify software behavior and signed request construction, not delivery to a real operational alert receiver.


# 54. Scoped consent grants and durable revocation

PR [#63](https://github.com/vkraj1123/Open-farm-intelligence/pull/63) introduced explicit service-scoped consent and a durable revocation ledger, merged to `main` as [`274b5f7b91bed12ef9741a71e3f3f7f84427a8d4`](https://github.com/vkraj1123/Open-farm-intelligence/commit/274b5f7b91bed12ef9741a71e3f3f7f84427a8d4). Schema migration v3 creates `ofi_consent_grants` with an opaque consent ID, actor ID, purpose, exact service scopes, status, grant time, optional expiry, and optional revocation time. PostgreSQL constraints and triggers make grant terms immutable, prevent deletion, and permit only the first revocation timestamp to be set.

The execution gateway now checks that the authenticated actor matches the grant owner, that the route exactly matches the requested action/service, that the grant is active at the time of authorization, and that the requested service is in the grant's scopes. If `PostgresConsentRepository` is injected, stored database state is authoritative at submission and when a prepared attempt is dispatched; a stale caller-held consent snapshot cannot widen scope or bypass a persisted revocation. Actor mismatch, expired/future/denied/revoked grants, and service-scope mismatch fail closed.

| Capability | Status | Boundary |
|---|---|---|
| Exact service scopes | Implemented and unit-tested | Empty explicit scopes keep backwards compatibility by using the purpose as a single narrow legacy scope; new callers should pass scopes explicitly |
| Grant/expiry/revocation timestamps | Implemented and unit-tested | All timestamps must be timezone-aware; grant expiry must follow grant time |
| Durable revocation | Implemented and PostgreSQL-tested | `PostgresConsentRepository` supports create/get/revoke; revocation is bound to the owning actor and stored grant ID |
| Immutable grant terms | Enforced in PostgreSQL | Only the first revocation timestamp can change; consent records cannot be deleted |
| Execution enforcement | Implemented and regression-tested | Applies at submission and prepared-attempt dispatch; cannot cancel an action already sent or in flight |
| Authenticated consent API | Implemented; farmer-facing UX remains open | `POST /consents`, `GET /consents/{consent_id}`, and `DELETE /consents/{consent_id}` require a verified bearer JWT; the signed `sub` is the actor, and creation requires `affirmative: true` plus unique explicit scopes. The other case/farm/evidence routes are not yet protected by this auth layer |
| Tenant-scoped persistence and access checks | Implemented by PR #69 | Core farm, case, consent, evidence-policy and assessment records are scoped by signed tenant ID and cross-tenant access is regression-tested |
| Tenant membership/RBAC and purpose limitation | Not completed | Trusted issuer must enforce membership before signing the tenant claim; no organization membership registry, membership-administration API, comprehensive roles, or downstream purpose gate exists yet |
| Production deployment | Not demonstrated | Use a shared durable repository; the in-memory repository is only a test/single-process development reference |

**Verification:** PR #63 passed standard CI ([run #714](https://github.com/vkraj1123/Open-farm-intelligence/actions/runs/38100581281)) and PostgreSQL-backed CI ([run #849](https://github.com/vkraj1123/Open-farm-intelligence/actions/runs/38100581299)). The PostgreSQL suite covers migration v3, consent-ledger immutability/revocation, and the recovery harness upgrading a v2 source to v3. These tests verify code and database behavior; they do not demonstrate that a real farmer-facing consent capture flow or multi-tenant security review exists.


# 55. Authenticated consent lifecycle API

PR [#65](https://github.com/vkraj1123/Open-farm-intelligence/pull/65) added the first authenticated HTTP lifecycle for consent grants and was merged to `main` as [`52053cc5d9a150f0cb1b320ef01e2dfb7dc5c0eb`](https://github.com/vkraj1123/Open-farm-intelligence/commit/52053cc5d9a150f0cb1b320ef01e2dfb7dc5c0eb).

The new `src/ofi/api_auth.py` verifier accepts compact HS256 JWTs only. It checks the HMAC signature, exact configured issuer and audience, a non-empty subject, a supported role, issued-at and expiry claims, optional not-before time, and a maximum 24-hour token lifetime. It requires `OFI_AUTH_HMAC_SECRET` (at least 32 UTF-8 bytes), `OFI_AUTH_ISSUER`, and `OFI_AUTH_AUDIENCE`; missing configuration fails closed. OFI validates tokens but does not issue them, so deployments must integrate a trusted issuer and protect/rotate secrets.

| Capability | Status | Boundary |
|---|---|---|
| Grant creation | Implemented and tested | `POST /consents`; farmer/cultivator/owner role only; explicit `affirmative: true`, purpose and unique scopes required |
| Actor binding | Implemented and tested | The JWT `sub` claim supplies the owner; body-supplied identity fields are rejected |
| Owner-only retrieval | Implemented and tested | `GET /consents/{consent_id}`; another actor receives the same 404 response as an unknown grant |
| Actor-bound revocation | Implemented and tested | `DELETE /consents/{consent_id}` records revocation without deleting the grant |
| Persistence | Configuration-dependent | PostgreSQL ledger when `OFI_DATABASE_URL` is set; otherwise process-local memory, which is not durable across restarts or processes |
| Resource authentication for other routes | Implemented by PR #67 | Case, farm, evidence and assessment routes require verified JWT authentication by default, except public `/health` and `/ready`; resource-level authorization is described in section #56 |
| Tenant-scoped core records and resource checks | Implemented in PR #69 | Signed `tenant_id` scoping covers farms, cases, consent grants, evidence policies and assessments |
| Tenant membership, full RBAC and purpose limitation | Not completed | The token issuer must verify membership; OFI has no organization membership registry, tenant-administration lifecycle, or downstream purpose gate yet |

**Verification:** PR #65 passed the standard workflow ([run #726](https://github.com/vkraj1123/Open-farm-intelligence/actions/runs/38101451663)) and PostgreSQL-backed workflow ([run #861](https://github.com/vkraj1123/Open-farm-intelligence/actions/runs/38101451611)). The tests exercise token validation, configuration fail-closed behavior, affirmative scoped grants, cross-actor denial, and revocation. They do not demonstrate a real farmer-facing interface, production token issuer, deployment key rotation, or tenant-wide access control.



# 56. Verified farm and case resource ownership

PR [#67](https://github.com/vkraj1123/Open-farm-intelligence/pull/67) added actor-bound access checks to case, farm, evidence and assessment endpoints. It was merged to `main` as [`4cd105e82569ce97de6414a77dd9dc113987ed90`](https://github.com/vkraj1123/Open-farm-intelligence/commit/4cd105e82569ce97de6414a77dd9dc113987ed90).

Resource routes now require the existing HS256 bearer-token verifier by default. The actor comes from the signed JWT `sub` and role claims. A farmer may access a farm whose `farmer_id` matches that actor; a different actor must be an active, explicitly verified land party whose role matches the token's `owner` or `cultivator` role. Unverified or expired party records do not confer access. Inaccessible farm/case identifiers return 404 to avoid disclosing whether the resource exists.

Case creation no longer acts as an implicit farm transfer or farm-profile update. When the farm already exists, the current actor must be authorized, the submitted primary farmer ID must match the stored value, and OFI uses the authoritative stored farm snapshot. Writes to global evidence-comparison policies are restricted to signed `agent`, `service_provider`, or `system` roles. Reading policies requires authentication; health and readiness endpoints remain intentionally public.

| Capability | Status | Boundary |
|---|---|---|
| Authenticated case/farm/evidence/assessment routes | Implemented and tested | JWT verification is required unless the explicit opt-out is set; `/health` and `/ready` remain public |
| Primary farmer ownership | Implemented and tested | Signed actor ID must match `farm.farmer_id` |
| Owner/cultivator party access | Implemented and tested | Requires matching role, current validity dates, and `verification: "verified"` |
| Cross-actor read/update denial | Implemented and regression-tested | Unknown and inaccessible farm/case IDs share 404 behavior |
| Farm takeover through case creation | Blocked and tested | Existing farmer identity/profile is not replaced by submitted case data |
| Global comparison-policy writes | Restricted and tested | Requires `agent`, `service_provider`, or `system` identity |
| Tenant-scoped core data | Implemented by PR #69 | Signed tenant_id scopes farm/case routes, consent grants, evidence policies, and assessments; PostgreSQL stores and filters the tenant boundary |
| Organization membership and legacy cutover | Not completed | Token issuer must verify membership; v4 legacy rows enter legacy:default and require an approved administrative mapping before multi-tenant cutover |
| Full RBAC, audit and purpose enforcement | Not completed | These controls still need a deliberate policy model and review |

Runtime resource authorization is enabled by default when `OFI_RESOURCE_AUTH_ENABLED` is unset. **Do not set it to `false` on a reachable deployment**; that opt-out exists only for isolated unit tests or a trusted local sandbox. JWT configuration is the same as in section #55: `OFI_AUTH_HMAC_SECRET`, `OFI_AUTH_ISSUER`, and `OFI_AUTH_AUDIENCE` are required. OFI validates the tokens but does not issue them.

**Verification:** PR #67 passed the [standard workflow](https://github.com/vkraj1123/Open-farm-intelligence/actions/runs/38103511050) and [PostgreSQL-backed workflow](https://github.com/vkraj1123/Open-farm-intelligence/actions/runs/38103511047). Tests cover anonymous rejection, cross-actor reads/writes, verified and unverified party cases, primary-farmer binding, farm takeover attempts, and policy-manager roles. These checks do not demonstrate independent organization tenancy, production identity-provider integration, or a completed security audit.



# 57. Tenant-scoped data and migration v4

PR [#69](https://github.com/vkraj1123/Open-farm-intelligence/pull/69) introduced the first explicit tenant boundary across core farm decision data and was merged to `main` as [`291b2c3211c92d17eadfae9227c87a25e79a57e0`](https://github.com/vkraj1123/Open-farm-intelligence/commit/291b2c3211c92d17eadfae9227c87a25e79a57e0). Schema migration v4 adds a tenant binding to farms, case records, consent grants, evidence-comparison policy heads/versions, and historical assessments. Existing migrations remain immutable; upgrades run through `ofi-schema`.

Protected resource and consent routes now require the verified HS256 bearer token to carry a non-empty signed `tenant_id` claim, alongside the existing subject and role claims. A request body cannot select or override its tenant. The trusted issuer must check that the actor is entitled to the tenant before issuing that claim; OFI validates the signature/claims but does not issue tokens or maintain an organization-membership registry.

Tenant-scoped policy IDs are independent: the same policy name may start at version 1 in two different tenants. PostgreSQL lookups for cases, consent grants, policy versions and assessment history include the tenant filter. Farm IDs remain globally unique; PostgreSQL refuses to rebind a farm or its parcel/crop/observation/contract IDs to another tenant/farm. Tenant columns on farms, cases, policy heads and consent grants are immutable to ordinary application updates.

| Capability | Status | Boundary |
|---|---|---|
| Tenant ID from verified JWT | Implemented and tested | Resource/consent routes reject missing tenant claims; tenant identity is never taken from request JSON |
| Farm and case access | Implemented and tested | Tenant match is checked alongside primary-farmer or active verified owner/cultivator relationship |
| Consent repository | Implemented and tested | Grant retrieval and revocation are tenant-filtered in addition to actor-bound |
| Evidence policies and assessments | Implemented and tested | Policy version namespace is (tenant ID, policy ID, version); assessment lookup includes tenant ID |
| PostgreSQL tenant binding | Implemented and tested | Migration v4 adds non-empty tenant columns, indexes and immutable-binding triggers |
| Upgrade compatibility | CI-tested | Existing records are backfilled into `legacy:default`; recovery harness now verifies v3→v4 upgrade without misreading tenant metadata as lost source data |
| Tenant membership and administration | Not completed | OFI has not implemented a tenant membership registry, invitation/assignment lifecycle, or an issuer integration that proves membership |
| Legacy-data cutover | Not completed | Do not issue `legacy:default` to ordinary users. Inventory and map existing records to approved real tenants through a controlled, audited migration before enabling tenant-facing production traffic |
| Full security governance | Not completed | Full RBAC, downstream purpose enforcement, audit, retention, encryption/TLS deployment policy and independent security review remain |

**Migration note:** `src/ofi/twin/schema.sql` remains a v1 compatibility/bootstrap schema. A PostgreSQL deployment must run `ofi-schema` to apply migrations through v4; `/ready` reports a schema-version mismatch when only the bootstrap script has been applied. Review tenant membership, existing record ownership, backup and restoration procedures before a production cutover.

**Verification:** PR #69 passed [standard CI](https://github.com/vkraj1123/Open-farm-intelligence/actions/runs/38105373898) and [PostgreSQL-backed CI](https://github.com/vkraj1123/Open-farm-intelligence/actions/runs/38105373969). Coverage includes missing tenant claims, same-actor cross-tenant denial, client-supplied tenant mismatch, independent policy namespaces, consent isolation, PostgreSQL farm takeover rejection, immutable tenant assignment, repository tenant filters and a v3→v4 restore/upgrade rehearsal. These checks do not demonstrate production tenant membership or a complete security audit.



# 58. Tenant-filtered farm repository reads

PR [#71](https://github.com/vkraj1123/Open-farm-intelligence/pull/71) adds a repository-level enforcement layer after the explicit tenant boundary in PR #69. The farm-twin repository contract now accepts an optional tenant scope. Both the in-memory implementation and PostgreSQL `PostGISFarmTwinStore` compare the requested tenant during lookup; PostgreSQL applies the `tenant_id` predicate in SQL instead of loading a farm from another tenant and relying only on a later API check.

Farm/case API reads pass the tenant ID from the verified JWT into the repository. The case-creation collision check remains deliberately non-disclosing: a farm ID that already belongs to another tenant returns 404, matching the response for an inaccessible or missing resource rather than exposing a cross-tenant collision through a 409 response. The Farm domain model field/validator ordering has also been cleaned up.

| Capability | Status | Boundary |
|---|---|---|
| Tenant-aware farm repository contract | Implemented and tested | Existing trusted internal callers may omit the optional tenant parameter; protected API reads pass the signed tenant explicitly |
| PostgreSQL tenant filtering | Implemented and tested | Tenant is part of the database `WHERE` predicate at farm lookup |
| In-memory behavior parity | Implemented and tested | An explicit mismatched tenant produces the same not-found behavior |
| Cross-tenant ID collision privacy | Implemented and regression-tested | Cross-tenant farm ID collision during case creation returns 404 rather than exposing a 409 |
| Broader tenant administration/RBAC | Not completed | Signed tenant claims still depend on trusted-issuer membership checks; membership lifecycle, full RBAC, audit and downstream purpose enforcement remain open |

**Verification:** PR #71 passed [standard CI](https://github.com/vkraj1123/Open-farm-intelligence/actions/runs/38106617569) and [PostgreSQL-backed CI](https://github.com/vkraj1123/Open-farm-intelligence/actions/runs/38106617589). This verifies repository/API behavior in test environments; production identity-provider integration, tenant membership lifecycle and an independent security review remain deployment/product work.


# 59. Tenant-scoped farm snapshots

This follow-up extends the tenant boundary from farm lookup to the farm snapshot read itself. The FarmTwinRepository.snapshot() contract now accepts an optional keyword-only tenant_id. Both the in-memory store and PostGISFarmTwinStore validate the requested tenant at the storage boundary before assembling crop-cycle, land-party, contract and observation data.

Authenticated API handlers now pass the verified token tenant to snapshot operations used by evidence collection, evidence-frame reporting, evidence comparison and persisted comparison assessment. The evidence-policy case-reasoning path also carries the same tenant scope through its case lookup, snapshot lookup and result retrieval. Existing trusted internal callers remain compatible by omitting the optional scope.

| Capability | Status | Boundary |
|---|---|---|
| Tenant-scoped in-memory snapshot | Implemented in branch | A mismatched tenant raises the same KeyError as a missing farm |
| Tenant-scoped PostgreSQL snapshot | Implemented in branch | Snapshot construction first requires a successful tenant-filtered farm query |
| Authenticated evidence API integration | Implemented in branch | Signed tenant identity is propagated to farm snapshot reads |
| Evidence-policy case reasoning | Implemented in branch | The case, farm snapshot and returned case record use the same tenant scope |
| Regression coverage | Passing in CI on implementation commit 74fd2e9 | Unit test plus PostgreSQL tenant-isolation test exercise matching and mismatched tenants |

**Verification:** All four GitHub Actions test runs passed on implementation commit [74fd2e9](https://github.com/vkraj1123/Open-farm-intelligence/commit/74fd2e9c714bad5a0f39e2112f18208dc4ea520c), including the PostgreSQL-backed regression run. The code changes covered by that commit passed; a subsequent documentation-only update is being rechecked by CI. These tests do not establish production token-issuer membership checks, live service-provider authorization, or a completed independent security audit. Keep the tenant-membership and legacy-data cutover limitations in section #57 in force.

# 60. Tenant-scoped farm-twin mutations

The farm-twin repository contract now allows crop-cycle registration, land-party insertion, production-contract insertion and observation insertion to carry an explicit keyword-only tenant scope. Both the in-memory and PostgreSQL implementations check the target farm with that tenant scope before mutating child records. Existing trusted internal callers remain compatible when omitting the optional scope.

The API passes the verified tenant claim when adding farmer observations and when collecting provider evidence. CaseManager mutation and evidence-collection paths also propagate tenant scope from the authenticated request or the stored case context, instead of relying only on an earlier API check.

| Capability | Status | Boundary |
|---|---|---|
| Scoped crop-cycle, land-party, contract and observation writes | Implemented in branch | Tenant mismatch returns the same not-found style KeyError before child data is changed |
| API observation writes | Implemented in branch | Signed tenant claim is propagated into CaseManager/farm-twin mutation calls |
| Regression coverage | Passing in CI on implementation commit e1cfab2 | In-memory tests cover all four mutations; PostgreSQL tenant-isolation tests cover mismatched-tenant rejection |

**Verification:** All four GitHub Actions runs passed on implementation commit [e1cfab2](https://github.com/vkraj1123/Open-farm-intelligence/commit/e1cfab2316f79309fd0735b360e2ed393e5ba7ef), including the PostgreSQL-backed tests on both push and pull-request workflows ([standard PR check](https://github.com/vkraj1123/Open-farm-intelligence/actions/runs/38110430618), [PostgreSQL PR check](https://github.com/vkraj1123/Open-farm-intelligence/actions/runs/38110430603), [standard push check](https://github.com/vkraj1123/Open-farm-intelligence/actions/runs/38110429070), [PostgreSQL push check](https://github.com/vkraj1123/Open-farm-intelligence/actions/runs/38110429085)). This documentation-only commit is being rechecked by CI. These tests do not implement organization membership verification in the token issuer or complete the outstanding RBAC and production security review.

# 61. Authoritative tenant membership and administration

Schema migration 005 adds a durable tenant-membership registry with active, suspended and revoked states, plus an append-only administration event log. When tenant-membership enforcement is required (the default), a valid signed tenant claim is not sufficient by itself: the actor must also have a current active membership record for the claimed tenant. Membership lookup failures fail closed rather than falling back to the token claim.

| Endpoint | Purpose | Authorization |
|---|---|---|
| POST /tenants/{tenant_id}/memberships | Grant or reactivate a member | Active tenant administrator; only platform system can grant tenant-admin role |
| GET /tenants/{tenant_id}/memberships | List the tenant's memberships | Tenant administrator or platform system |
| PATCH /tenants/{tenant_id}/memberships/{member_actor_id} | Change role or suspend/revoke/reactivate membership | Tenant administrators manage regular members; platform system manages administrator roles |
| GET /tenants/{tenant_id}/membership-events | Read membership administration history | Tenant administrator or platform system |

The repository prevents revoking, suspending or demoting the last active tenant administrator and records each state change in an append-only audit table. A trusted platform-system token is the bootstrap authority for the first tenant administrator. Provision actor IDs only from the deployment's trusted identity provider; this API is not an invitation/email-verification service.

| Capability | Status | Boundary |
|---|---|---|
| Durable tenant membership registry | Implemented in branch | PostgreSQL migration 005; in-memory variant is for tests and single-process development only |
| Current membership check on resource and consent API authentication | Implemented in branch | Missing, suspended or revoked membership is denied; storage/configuration errors fail closed |
| Tenant membership administration | Implemented in branch | Tenant scoping, role checks, last-admin invariant and audit events |
| Verification | Passing in CI on hardened commit 2f4261c | Membership API and PostgreSQL repository regressions run with the full suite |

**Security boundary:** this implements an authoritative OFI membership registry, but does not replace upstream identity proofing, SSO/MFA, tenant invitations/verification, or independent production security review. Keep OFI_TENANT_MEMBERSHIP_ENFORCEMENT at its default required value outside tests. Disabled mode is permitted only when OFI_ENV=test.

**Verification:** All four GitHub Actions runs passed on hardened commit [2f4261c](https://github.com/vkraj1123/Open-farm-intelligence/commit/2f4261c25457e8632f70f7e31fb5e2e8894f30ba): [standard PR check](https://github.com/vkraj1123/Open-farm-intelligence/actions/runs/38111543850), [PostgreSQL PR check](https://github.com/vkraj1123/Open-farm-intelligence/actions/runs/38111543834), [standard push check](https://github.com/vkraj1123/Open-farm-intelligence/actions/runs/38111541411), and [PostgreSQL push check](https://github.com/vkraj1123/Open-farm-intelligence/actions/runs/38111541402). This tracker update is documentation-only and is being rechecked by CI.

# 62. Opt-in OIDC/JWKS bearer-token verification

The API authentication boundary now supports an opt-in OIDC/JWKS verification mode while preserving HS256 as the default for existing deployments. In `oidc_jwks` mode, OFI accepts RS256-signed access tokens only, resolves keys from a configured HTTPS JWKS endpoint, and validates the signature, exact issuer, audience, subject, time bounds, maximum 24-hour token lifetime, and an explicitly mapped OFI role claim. Role and tenant claim names are configurable; no arbitrary group list is treated as an administrator role.

| Capability | Status | Boundary |
|---|---|---|
| Existing HS256 verifier | Preserved and regression-tested | Remains the default; configuration requirements are unchanged |
| OIDC/JWKS RS256 verifier | Implemented and unit-tested with generated RSA keys | Synthetic local key selection validates signature/claim behavior; a real provider has not been configured |
| Issuer/audience/algorithm constraints | Implemented and tested | Exact issuer and audience; RS256 only; token lifetime capped at 24 hours |
| JWKS resilience | Implemented | Five-minute key-set cache; unknown key IDs trigger refresh; key-endpoint connection failure maps to sanitized 503 |
| Role/tenant claim mapping | Implemented | External roles must map to one explicit OFI role; tenant routes still require active membership in OFI's registry |
| Deployment documentation | Added in `docs/identity-provider.md` | Runtime must install the `auth` extra and use trusted environment/secret configuration |
| Real identity-provider integration | Not verified | Needs provider-issued staging tokens, deployment network/TLS checks and security review |
| Invitation, email verification, MFA and identity lifecycle | Not implemented by this milestone | Remain identity-provider/tenant-onboarding work |

Configure `OFI_AUTH_MODE=oidc_jwks`, `OFI_AUTH_JWKS_URL`, `OFI_AUTH_ISSUER`, and `OFI_AUTH_AUDIENCE` only after testing with an approved staging issuer. See the [identity-provider runbook](docs/identity-provider.md). Keep the authoritative tenant-membership check enabled; a valid signed tenant claim never overrides a suspended or revoked membership.

**Verification boundary:** unit tests use synthetic RSA keys and a stub key resolver, not a real identity provider. Passing CI proves the checked-in validator behavior, not that SSO/MFA, tenant provisioning or a production issuer is configured.

# 63. OIDC runtime-extra packaging contract

Milestone #62 documented installation with `python -m pip install -e ".[auth]"`, but the `auth` optional dependency group was not declared in package metadata. This milestone declares the runtime `auth` extra with `PyJWT[crypto]` and adds an independent GitHub Actions workflow that installs that extra, then runs the bearer-token verification tests without installing the development extra first.

| Capability | Status | Boundary |
|---|---|---|
| Documented `.[auth]` extra | Added to package metadata | Installs PyJWT and its cryptographic dependencies required by OIDC/JWKS verification |
| Isolated install-path regression check | Added to CI | Installs `.[auth]` and pytest separately, then runs `tests/test_api_auth.py` |
| Live OIDC provider integration | Still unverified | Needs a trusted staging issuer, provider-issued tokens and deployment network/TLS checks |

This closes a packaging/documentation mismatch. It does not configure a real issuer, enable OIDC mode in any deployment, or replace identity-provider and security review.

# 64. Tenant invitation lifecycle

Migration 006 adds a tenant-scoped invitation lifecycle. Tenant administrators (or the trusted platform-system authority) can create a time-limited invitation for a specific identity-provider subject, list invitation metadata, revoke an unused invitation and inspect invitation audit events. Invitations always grant the regular \`member\` role; this API cannot grant tenant-administrator privileges.

| Endpoint | Purpose | Authorization |
|---|---|---|
| POST \`/tenants/{tenant_id}/invitations\` | Issue an invitation for a stable identity-provider \`sub\` | Tenant administrator or platform system |
| GET \`/tenants/{tenant_id}/invitations\` | List invitation metadata | Tenant administrator or platform system |
| POST \`/tenants/{tenant_id}/invitations/redeem\` | Redeem a one-time secret | Authenticated, matching invited subject |
| POST \`/tenants/{tenant_id}/invitations/{invitation_id}/revoke\` | Revoke a pending invitation | Tenant administrator or platform system |
| GET \`/tenants/{tenant_id}/invitation-events\` | Read append-only lifecycle audit events | Tenant administrator or platform system |

The invitation token is generated with a cryptographically secure random generator; only its SHA-256 digest is persisted. In manual mode the raw token is returned once by the create endpoint; in webhook mode it is sent only to the configured signed HTTPS delivery bridge and is not returned to the API caller. The token is not included in list responses or audit events. The subject identifier must be the trusted issuer's stable \`sub\` claim, not a client-supplied email. Redemption verifies the caller's authenticated subject, tenant path, pending state and expiry. Membership creation/reactivation, membership audit, invitation consumption and invitation audit commit in one PostgreSQL transaction. A token cannot be redeemed twice, by another subject, or against another tenant. Pending invitations expire after a bounded duration (1 hour to 30 days; default 7 days) and can be revoked.

**Delivery and identity boundary:** OFI does not send invitation email itself, prove ownership of an email address, create identity-provider accounts, or issue authentication tokens. Manual mode requires the caller to deliver the returned token securely. Optional webhook mode sends the token to a configured trusted bridge, which must map the stable identity-provider subject to an approved delivery channel and protect the token from logs and unauthorized access. An identity provider must authenticate that subject and map it to the same stable \`sub\` claim. Invitation redemption is an onboarding primitive, not a complete OIDC login/signup flow. Email verification, MFA policy, provider-side membership/claim synchronization, edge rate limiting, and independent security review remain outstanding.

| Capability | Status | Boundary |
|---|---|---|
| Hashed, expiring, single-use invitations | Implemented | No raw token stored or returned by list endpoints |
| Intended-subject and tenant binding | Implemented | Redemption requires valid authentication and exact subject/tenant match |
| Revocation and lifecycle audit | Implemented | Append-only SQL audit table; expiry is recorded when inspected or redeemed |
| Atomic redemption with membership creation/reactivation | Implemented | PostgreSQL transaction keeps invitation consumption, membership and both audit events consistent |
| Live identity provider and notification delivery | Not verified | Requires deployment integration; no provider/email service configured |

# 65. Invitation response and concurrent-redemption hardening

This follow-up closes two narrow security-validation gaps in the invitation lifecycle.

| Capability | Status | Evidence / boundary |
|---|---|---|
| One-time token response caching | Hardened | Invitation creation sets \`Cache-Control: no-store, private\` and \`Pragma: no-cache\`; API test asserts both headers |
| Concurrent redemption | Integration-tested | Two PostgreSQL transactions race on the same invitation; exactly one may redeem it |
| Membership and audit consistency under replay | Integration-tested | Exactly one membership row and one membership-creation event are expected after the race |
| Secret persistence check | Integration-tested | Database stores the SHA-256 digest, not the raw token |
| External delivery / email verification | Still outstanding | No delivery provider, identity lifecycle, MFA or live issuer has been configured |

The raw invitation token is a short-lived credential, so the create response explicitly prohibits HTTP caching. PostgreSQL's row-level lock during redemption is validated with concurrent independent connections, rather than relying only on sequential tests or the in-memory lock. CI evidence does not substitute for live identity-provider, delivery-channel or independent security testing.

# 66. Signed invitation-delivery bridge

OFI now supports an opt-in provider-neutral delivery webhook so a deployment can connect its own email/notification system without coupling invitation management to one vendor. Set \`OFI_INVITATION_DELIVERY_MODE=webhook\` to enable it. The bridge receives a versioned JSON event over HTTPS, signed with HMAC-SHA256 over the exact request body. It receives the one-time token, invitation ID, tenant ID, stable target subject and expiry; it is responsible for resolving the subject through a trusted directory and delivering the token through an approved channel.

| Capability | Status | Security boundary |
|---|---|---|
| Signed outbound HTTPS delivery | Implemented and unit-tested | HTTPS only; URL credentials/query/fragments rejected; redirects are not followed |
| Token handling | Implemented and API-tested | Token is not returned to the API caller in webhook mode; response cache is disabled |
| Delivery acceptance and failure | Implemented and API-tested | Only 2xx counts as accepted; failed/ambiguous delivery triggers revocation and no automatic retry |
| Durable-state requirement | Enforced | Webhook mode refuses the in-memory invitation repository |
| Provider/email integration | Not yet verified | Deployment must install/configure a trusted bridge, delivery channel, secret rotation and monitoring |

See [the invitation delivery runbook](docs/tenant-invitation-delivery.md) for receiver verification and deployment configuration. The webhook is an integration contract, not a built-in mail server. It does not by itself prove email ownership, synchronize identity-provider memberships, or establish that a real delivery provider is operational.


# 67. Phone-launchable staging invitation acceptance

A manual GitHub Actions workflow now provides a guarded staging check that can be started from GitHub's mobile app/browser. It verifies health/readiness, creates one one-hour invitation for a configured disposable identity, checks no-store headers and token-free webhook-mode responses, confirms the API's delivery-acceptance event ID, and verifies that invitation metadata remains in durable storage. It deliberately does not print API response bodies, credentials, or invitation tokens, and refuses redirect targets.

| Capability | Status | Evidence / boundary |
|---|---|---|
| Launch from GitHub without a local development machine | Implemented | Manual workflow dispatch; Python standard library only |
| Staging environment separation | Implemented in workflow contract | Dedicated \`ofi-staging\` GitHub environment, protected branch check, explicit typed confirmation |
| Safe URL, TLS, placeholder and redirect handling | Implemented and unit-tested | HTTPS origin only; documentation-only hostnames, obvious local/private IP targets and redirects rejected; default TLS verification retained |
| Token-free response and durable-state checks | Implemented and unit-tested | Checks no-store headers, secret-like response fields, delivery event ID and invitation metadata |
| Real delivery receipt and subject-bound redemption | Not yet verified | Requires staging deployment, environment secrets, bridge operations view and a disposable test identity |

See the [phone-launchable staging acceptance guide](docs/staging-invitation-acceptance.md). A green workflow will prove the staging API accepted the webhook handoff; it will not by itself prove mailbox delivery or successful redemption. Do not configure production credentials in this workflow.

# 68. Phone-first Render API deployment starter

Added a Render Blueprint and startup entrypoint to move OFI from CI-only code to a reachable FastAPI staging service without requiring a local PC. The Blueprint provisions a temporary Render web service and PostgreSQL database; the startup command applies OFI's ordered, checksum-verified schema migrations before starting Uvicorn. The web service uses `/health` as its liveness check and keeps resource authentication enabled.

| Capability | Status | Evidence / boundary |
|---|---|---|
| Render Blueprint | Added in this milestone | `render.yaml` provisions a Python web service and PostgreSQL in Singapore |
| Startup and schema migration | Added in this milestone | `scripts/render_start.sh` requires `OFI_DATABASE_URL`, runs `ofi-schema`, then binds Uvicorn to `0.0.0.0:$PORT` |
| Phone-first deployment instructions | Added in this milestone | [Render deployment guide](docs/deploy-to-render.md) and Deploy to Render button |
| Live service and database | Not yet verified | Repository changes cannot provision cloud resources without the owner's Render/GitHub authorization |
| Durable long-lived staging / production | Not provided by the included free configuration | Render documents a 30-day expiry for Free Postgres; upgrade or select a database plan with appropriate retention/backups before keeping data |
| Live identity provider and invitation delivery bridge | Still outstanding | Auth issuer is deliberately non-routable; delivery remains in manual mode until a trusted provider bridge is deployed |

**Deployment acceptance update (2026-10-16):** The owner launched the Render Blueprint service `ofi-staging-api` and ran the manual [staging API readiness workflow](https://github.com/vkraj1123/Open-farm-intelligence/actions/workflows/staging-api-readiness.yml). The workflow passed, confirming the deployed service responded as required on `/health` and `/ready`, including durable PostgreSQL, PostGIS, and complete current schema relations. The staging invitation workflow remains blocked until a trusted identity issuer and signed delivery bridge are configured. This does not establish production readiness, backup suitability, live authentication, or invitation delivery/redemption.


# 69. Staging URL placeholder fail-fast validation

The staging invitation smoke check now rejects documentation-only hostnames (`example.com`, `example.net`, `example.org`, `.example`, `.invalid`, and `.test`) before making a network request. The phone runbook now states that deployment and `/health` + `/ready` verification must happen first, and that invitation acceptance additionally requires a real identity-provider configuration and trusted signed delivery bridge.

| Capability | Status | Evidence / boundary |
|---|---|---|
| Placeholder-host detection | Added and regression-tested | Fails before network access with an actionable configuration error |
| Deployment prerequisite clarity | Updated | Invitation guide links the Render setup and distinguishes API deployment from delivery-provider acceptance |
| Live staging deployment | Still unverified | Owner must authorize the Render Blueprint and verify the actual hostname and PostgreSQL readiness |

# 70. Phone-launchable API deployment readiness check

A read-only manual GitHub Actions workflow now lets the repository owner verify a live Render API entirely from a phone, before any invitation or identity-provider setup is attempted.

| Capability | Status | Evidence / boundary |
|---|---|---|
| Phone-first endpoint smoke check | Added | Manual `workflow_dispatch`; accepts the actual Render service origin |
| Health and readiness validation | Added and unit-tested | Requires `/health` = `ok`; `/ready` = ready with PostgreSQL, PostGIS, durable storage, and complete current schema |
| URL and network safeguards | Added and unit-tested | HTTPS only; Render `.onrender.com` host only; placeholder/path/query/credential/port ambiguity rejected; redirects are not followed |
| Secret and data exposure | Designed to be read-only | No credentials or invitation data required; response bodies are not printed |
| Live service verification | Passed on 2026-10-16 | Owner ran the manual readiness workflow against `ofi-staging-api.onrender.com`; the green job verifies `/health`, durable PostgreSQL/PostGIS, and complete current schema relations |

See the [phone-first Render deployment guide](docs/deploy-to-render.md) and [Staging API readiness workflow](https://github.com/vkraj1123/Open-farm-intelligence/actions/workflows/staging-api-readiness.yml). A green readiness workflow proves deployment/database readiness only; it does not prove live authentication, invitation delivery, redemption, backups, or production readiness.



# 71. Staging readiness workflow runtime maintenance

The first live Render staging readiness run passed on 2026-10-16. Its annotation also exposed an upcoming runner transition, so the workflow is being updated to use current Node.js 24-compatible actions and a stable Ubuntu runner label rather than relying on the moving `ubuntu-latest` label.

| Capability | Status | Evidence / boundary |
|---|---|---|
| Node.js action runtime | Implemented and merged in PR #85 | `actions/checkout@v7` and `actions/setup-python@v7` were downloaded and initialized successfully in the post-merge run |
| Runner image determinism | Implemented and merged in PR #85 | The post-merge run executed on `ubuntu-24.04` |
| Runtime migration | Verified in GitHub Actions | Post-merge run used Ubuntu 24.04, `actions/checkout@v7`, and `actions/setup-python@v7`; the endpoint request then timed out separately |

This maintenance does not alter the API contract or database checks. Passing readiness remains separate from production hardening, backup retention, identity-provider setup, and invitation delivery/redemption.


# 72. Render free-tier cold-start retry handling

The live readiness workflow has a successful run (#1 at 2026-10-16 02:37:43 UTC) followed by run #2 failing to reach `/health` at 02:55:34 UTC, 17 minutes 51 seconds later. Render documents that Free web services spin down after 15 minutes of inactivity and take about one minute to start again ([Render Free service limitations](https://render.com/docs/free#spinning-down-on-idle)). The previous smoke check used a single 12-second request timeout, too short for this expected cold-start path.

| Capability | Status | Evidence / boundary |
|---|---|---|
| Root-cause hypothesis | Evidence-supported | Run timing matches Render's 15-minute idle spin-down window; failure was an HTTPS reachability timeout, not a workflow-runtime failure |
| Transient startup retry | Implemented and merged in PR #86 | Up to four attempts per endpoint with bounded waits; retries only network failures and gateway statuses 502/503/504 |
| HTTPS and redirect safeguards | Preserved | TLS verification remains enabled; redirects are still rejected; permanent HTTP errors fail immediately |
| Regression coverage | Implemented and merged in PR #86 | Offline tests for temporary network failure, transient 503 recovery, and non-retryable 404 passed in CI |
| Cold-start recovery in live Render | Passed on 2026-10-16 | Manual readiness run #3 passed after PR #86; `/health` returned HTTP 200 and `/ready` confirmed PostgreSQL, PostGIS, durable storage, and schema version 6/6 ([run #3](https://github.com/vkraj1123/Open-farm-intelligence/actions/runs/38844036717)) |

The readiness workflow remains read-only. The retry budget is bounded so the job stays within its three-minute timeout in the typical cold-start case. If all retries fail, inspect Render's service events, deploy logs, and runtime logs to distinguish an asleep service from a startup/database crash. Readiness still does not certify identity-provider integration, invitation delivery/redemption, backups, or production readiness.


# 73. Phone-launchable OIDC provider metadata preflight

The live Render staging API now passes the bounded-retry readiness check, including schema version 6/6. The next integration boundary is selecting and configuring a trusted OIDC provider. A read-only manual GitHub Actions workflow now checks the provider's discovery metadata and public signing-key set from a phone before any Render authentication change is made.

| Capability | Status | Evidence / boundary |
|---|---|---|
| Issuer/JWKS metadata agreement | Implemented in this milestone | Discovery `issuer` and `jwks_uri` must exactly match the operator-supplied values |
| Public signing-key preflight | Implemented in this milestone | Requires unique, public-only RSA signing keys compatible with RS256 and at least 2048-bit modulus |
| Network and secret safeguards | Implemented in this milestone | HTTPS with verified TLS; redirects rejected; query/userinfo/fragment and IP-literal targets rejected; DNS answers checked for non-public IPs; response bodies and key material are never printed |
| Phone-first operator flow | Implemented in this milestone | Manual workflow; no credentials, provider secrets, or local computer required |
| Public metadata preflight | Passed on 2026-10-16 | Google issuer and JWKS matched exactly; two public RSA signing keys passed RS256/2048-bit checks ([run #1](https://github.com/vkraj1123/Open-farm-intelligence/actions/runs/38846299491)) |
| Live identity-provider integration | Still pending | Google's metadata has been tested, but final token issuer/broker remains unselected; metadata alone does not validate API audience, OFI role/tenant claims, user login, tenant membership, or invitation delivery |

See the [OIDC provider preflight runbook](docs/oidc-provider-preflight.md) and [manual preflight workflow](https://github.com/vkraj1123/Open-farm-intelligence/actions/workflows/oidc-provider-preflight.yml). Do not change the deployed API to OIDC/JWKS mode just because metadata checks pass; select a provider, configure exact issuer/audience/JWKS settings, and complete real staging token and tenant-authorization tests first.


# 74. Google OIDC token compatibility decision

The phone-first metadata preflight passed against Google's public issuer and JWKS endpoints on 2026-10-16. Reviewing OFI's existing verifier after that success established the next safety boundary: OFI accepts RS256 bearer tokens only in opt-in OIDC mode, checks a configured API audience, and requires a role claim from OFI's allow-list. Tenant-scoped access still depends on the authoritative OFI membership registry.

| Capability | Status | Evidence / boundary |
|---|---|---|
| Google discovery and signing-key reachability | Verified | [OIDC metadata preflight run #1](https://github.com/vkraj1123/Open-farm-intelligence/actions/runs/38846299491) |
| Native Google token compatibility with OFI authorization claims | Not established; do not assume | Google's standard ID-token reference documents the issuer, audience, subject and time claims but not OFI's `role` or `tenant_id`; its ID-token audience is an OAuth client ID |
| Safe provider architecture | Decision required | Select a trusted API token issuer/broker capable of issuing OFI-audience RS256 access tokens with authorization claims from trusted sources, or approve a reviewed alternative identity-mapping architecture |
| Render auth-mode change | Not performed | Staging remains on its existing HS256 configuration; no auth variables were modified by the preflight |

See [OIDC provider preflight](docs/oidc-provider-preflight.md), [OFI token verification contract](docs/identity-provider.md), and the [Google OIDC ID-token reference](https://developers.google.com/identity/openid-connect/reference). Do not treat a successful discovery/JWKS check as proof of real login or tenant authorization. A user email, hosted domain, or request-supplied role must never grant an OFI role or tenant membership.


# 75. Google + phone-number SMS OTP staging sign-in plan

The requested staging sign-in plan now includes two authentication methods through a proposed Auth0 broker: Google social sign-in and passwordless phone-number SMS OTP. The runbook documents provider configuration, phone-provider setup, the OFI API audience/token contract, and the required security boundary between proving an identity and granting farm/tenant access.

| Capability | Status | Evidence / boundary |
|---|---|---|
| Google sign-in setup path | Documented | Auth0 Google social connection; owner must supply/configure their own OAuth application credentials in provider dashboards |
| Phone-number sign-in | Documented; provider setup pending | Auth0 Passwordless SMS OTP; requires a configured supported SMS provider, budget/rate limits, and phone number controlled by the staging tester |
| Optional verified phone claims | Template added | `deploy/auth0/phone-claims-action.js` emits namespaced `phone_number` and `phone_number_verified` only when Auth0 says the phone is verified |
| Trusted role/tenant claims | Not automatically provisioned | Phone number, email, Google domain, and editable user metadata must never assign an OFI role or tenant; use a trusted authorization source plus OFI's membership registry |
| Auth0 tenant/application configured | Pending owner setup | No Auth0 credentials or SMS provider credentials have been supplied to or committed in the repository |
| Render auth-mode change | Not performed | Keep staging's existing `hs256` mode until Auth0 issuer/JWKS, API audience, authorization claims, and real staging token checks pass |

See the [Google + SMS OTP Auth0 staging runbook](docs/auth0-google-sms-staging.md), [OIDC integration guide](docs/identity-provider.md), and optional [verified-phone Auth0 Action template](deploy/auth0/phone-claims-action.js). The Google metadata test passed, but Auth0's own discovery issuer/JWKS preflight and an actual OFI-audience access token are still required. Invitation delivery and redemption remain separate acceptance gates.


# 76. Phone-first Auth0 sign-in client scaffold

The staging frontend now has a responsive static sign-in page with separate Google and phone/SMS-code entry points. A Render Blueprint resource `ofi-staging-web` publishes the static `web/` directory, and the setup runbook describes the exact Auth0 SPA callback/origin configuration flow for a phone-only contributor.

| Capability | Status | Evidence / boundary |
|---|---|---|
| Mobile-first sign-in screen | Implemented in proposed change | Responsive, keyboard-focusable page with Google and SMS OTP options, status messaging, identity confirmation and sign-out |
| Auth0 login redirects | Implemented in proposed change | Auth0 SPA SDK redirect flow uses the configured `google-oauth2` and `sms` connections; callback state is handled and OAuth query parameters are removed afterward |
| Token exposure controls | Implemented in proposed change | SDK memory cache, no refresh-token persistence configured, no token output/logging, no manual browser storage |
| Static staging deployment | Implemented in proposed change | Render Blueprint includes `ofi-staging-web` with restrictive response headers and a Content Security Policy |
| Unconfigured state | Implemented in proposed change | Blank Auth0 domain/client ID leave sign-in buttons disabled; no fake identity-provider config is committed |
| Regression checks | Added in proposed change | Python test suite asserts Google/SMS paths, disabled defaults, token-handling safeguards, authorization disclosure and Blueprint security headers |
| Live Auth0 configuration | Pending owner setup | Needs a staging Auth0 tenant, SPA application, enabled Google and SMS connections, API audience `ofi-api`, and allowed URLs that match Render's actual static-site hostname |
| Actual OFI API authorization | Not yet tested by this scaffold | Login and token issuance do not validate OFI role/tenant claims or active membership; API auth mode remains HS256 and protected API calls are intentionally out of scope here |

After merge, use Render's existing Blueprint **Manual sync** to add `ofi-staging-web`. Copy its actual hostname, register it as the Auth0 SPA's exact callback/logout/origin, then update only the public domain and Client ID in `web/auth-config.js` via a reviewed GitHub change. Deploy that static-site commit from Render. Do not change the API's auth mode until the Auth0 token issuer/audience/claim design and real authorization tests are completed.

# 77. Auth0 staging SPA public configuration

The staging browser client configuration uses the public Auth0 tenant hostname and owner-created first-party Single Page Application client ID. The exact Render static-site origin is documented for the Auth0 application URL allow-list.

| Capability | Status | Evidence / boundary |
|---|---|---|
| Public Auth0 browser configuration | Added in this milestone | `web/auth-config.js` contains only the tenant hostname and public SPA client ID; no client secret, SMS-provider credential, OTP, or token |
| Render site origin | Identified | `https://ofi-staging-web.onrender.com` must be registered exactly in Allowed Callback URLs, Allowed Logout URLs, and Allowed Web Origins |
| Config regression coverage | Updated in this milestone | Tests validate non-empty hostname-shaped domain and client ID and reject secret-like configuration keys |
| Auth0 connection/API setup | Still requires dashboard verification | Google and SMS connections must be enabled for the SPA; the custom OFI API Identifier must match `ofi-api` |
| Live sign-in/token validation | Pending | A configured page does not prove callback allow-list correctness, SMS delivery, API-audience token issuance, or OFI authorization |
| API authentication mode | Unchanged | Staging API remains on HS256; do not switch it until issuer/JWKS/audience and trusted role/tenant authorization tests pass |

The owner must confirm the site origin is in all three Auth0 URL allow-lists, Google and SMS are enabled for the app, and an Auth0 API with Identifier `ofi-api` exists. After the reviewed change is merged, manually deploy the latest commit to the Render static site, then perform a test sign-in using a controlled test identity. Never share an OTP, secret, or access token in chat or logs.


# 78. Auth0 CDN SDK global resolution fix

The live staging sign-in page displayed the Auth0 SDK-load error after the public SPA configuration was deployed. Code inspection identified a browser-global mismatch: the official Auth0 CDN bundle exposes `createAuth0Client` under `window.auth0`, while the client was checking `window.createAuth0Client` at the top level.

| Capability | Status | Evidence / boundary |
|---|---|---|
| SDK global lookup | Fixed in proposed change | Client checks and invokes `window.auth0.createAuth0Client`, matching Auth0's CDN API |
| Regression test | Added in proposed change | Test checks the CDN URL and ensures the client uses the `window.auth0` namespace rather than the incorrect top-level global |
| Live browser verification | Pending | Requires merging, manual deployment to Render, and reloading the deployed site; the code fix alone does not prove the network/CDN is reachable from every client network |
| Identity provider setup | Still pending verification | Google/SMS connection settings, application allow-lists, and the `ofi-api` audience still need to be verified in Auth0 |
| API authentication mode | Unchanged | Staging API remains on HS256; no API environment variables or authorization policy are changed by this frontend fix |

Source: Auth0 SPA SDK documentation and FAQ show the CDN global as `auth0.createAuth0Client`. See the [SDK documentation](https://auth0.github.io/auth0-spa-js/) and [SDK FAQ](https://github.com/auth0/auth0-spa-js/blob/main/FAQ.md).
