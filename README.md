# Open Farm Intelligence

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
│   │   └── schema.sql
│   │       # Farm Digital Twin and persistence boundary
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

### Current execution boundary

The callback path verifies HMAC-SHA256, binds typed fields to signed payloads, validates ownership and applies events to the named attempt. Reconciliation evidence is signed, freshness-checked, bound to the exact provider/transaction/attempt, durably deduplicated, and applied atomically. Attempt-level audit events now record creation, status transitions, provider callback application and reconciliation decisions. PostgreSQL writes the state transition and its trigger-generated audit event in the same transaction; the audit table rejects row updates/deletes. Provider-specific production adapters, secret rotation, production scheduler configuration, external idempotency guarantees and operational alerting remain open.

### Next reliability milestone

The concurrency and audit-failure milestone has in-memory and real-PostgreSQL regression coverage. The canonical correlation ID is the durable transaction ID; the provider request ID is the exact attempt ID. Both are exposed consistently on execution requests/receipts, callbacks, reconciliation evidence and audit events without duplicating stored identifiers. The dispatch recovery report exposes low-cardinality outcome/error counters, and `src/ofi/services/recovery_health.py` provides a deterministic assessment against explicit deployment thresholds for errors, unknown attempts and pending reconciliations. Unit tests cover healthy, warning, critical, pending and invalid-threshold behavior. Still open: wire the pure assessment into the chosen deployment's logs/metrics/alert receiver; select and document real scheduler cadence, timeout/backoff and alert thresholds; validate real provider adapters and provider-supported external idempotency. No universal thresholds or deployment platform are assumed. Preserve the invariant that audit writes and state changes commit or roll back together.

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
- [ ] Multi-farm tenant isolation validation
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
- [ ] Deployment scheduler configuration and operational alerting
- [ ] Operational metrics and alerts
- [ ] Provider-supported external idempotency and real service adapter
- [ ] Production dispatch observability and secret management

**Status: 🟡 Guarded retry, one-time dispatch claims, stale-claim recovery, reconciliation worker and attempt-level audit history are implemented; production scheduling, provider idempotency, operational alerting and real integrations remain**

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
- [ ] Consent scopes
- [ ] Purpose limitation
- [ ] Consent expiry
- [ ] Revocation
- [ ] Encryption at rest
- [ ] Encryption in transit configuration
- [ ] Audit log
- [ ] Role-based access control
- [ ] Tenant isolation testing
- [ ] Data retention policy
- [ ] Privacy/data-sharing policy
- [ ] Security audit

**Status: 🟡 Architectural foundation only**

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
