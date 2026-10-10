# Deterministic Evidence Alignment

## Purpose

The service in `ofi.services.evidence_alignment` creates a deterministic evidence frame for a farm snapshot. It is a data-quality and eligibility boundary before reasoning, not an agronomic model.

It records:

- observation freshness relative to the snapshot time;
- whether a crop-cycle-specific observation belongs to the active crop cycle;
- duplicate observation identifiers;
- whether provenance metadata is present;
- explicit reasons why an observation was excluded;
- source, quality and confidence values without recalibrating or combining them.

## Freshness policy is configuration

Callers must supply `max_age_by_kind`, mapping each observation kind to a positive `timedelta`. OFI intentionally does not prescribe one universal freshness window for weather, soil tests, satellite imagery, farmer reports or market data. Domain owners must define and version policies for their data sources and intended decisions.

An observation whose kind has no configured window is marked `unconfigured` and excluded from the eligible set. A future-dated observation is excluded. An observation is stale only when its age is strictly greater than the configured window.

## Crop-cycle scope

An observation with a non-null `crop_cycle_id` must match the active crop cycle in the snapshot. An observation with no crop-cycle ID is treated as farm-scoped and may remain eligible; callers should use `require_provenance=True` when they need to require explicit supporting provenance.

## Provenance

An empty provenance object is always reported with `provenance_missing`. By default this is a quality warning and does not alone exclude an otherwise eligible observation. With `require_provenance=True`, it is excluded and also receives `provenance_required`.

The service does not verify that a provider is authoritative, that provenance claims are true, or that a timestamp is cryptographically trustworthy. Those checks belong at ingestion/provider boundaries.

## Output and safety

The returned `EvidenceFrame` includes a stable time-descending list of assessments, eligible observation IDs and counts for exclusion reasons. Quality and confidence remain separate inputs; neither is a calibrated probability. This service does not infer contradictions between values, make crop-treatment recommendations, or mutate the farm twin.

## Example

```python
from datetime import timedelta
from ofi.services.evidence_alignment import align_observations

frame = align_observations(
    snapshot,
    observations,
    max_age_by_kind={
        "weather": timedelta(hours=24),
        "soil": timedelta(days=90),
        "satellite": timedelta(days=14),
        "farmer_report": timedelta(days=7),
    },
    require_provenance=True,
)
```

The windows above are illustrative only; they are not endorsed defaults for a real deployment. Production use requires reviewed, source-specific policy and tests.

## HTTP API

The endpoint `POST /farms/{farm_id}/evidence-frame` exposes a one-time eligibility report for the current farm snapshot. The caller must provide a positive freshness window for each observation kind it wants to consider; OFI does not silently invent default windows.

Example request:

```json
{
  "max_age_seconds_by_kind": {
    "weather": 86400,
    "soil": 7776000,
    "satellite": 1209600
  },
  "require_provenance": true
}
```

These values are examples only, not endorsed defaults. The response includes the snapshot time, crop-cycle ID, eligible observation IDs, per-observation freshness and eligibility, issue codes, and exclusion counts. Unknown farm snapshots return 404; invalid policies return 422. The endpoint does not mutate observations or the farm twin.

## Current boundary

The alignment report is deterministic for the captured snapshot time and supplied policy, but remains in-memory and request-scoped. It does not persist alignment assessments, compare evidence across providers, infer scientific contradictions, provide source-specific policy versioning, or automatically filter the orchestrator's existing reasoning input. A caller must explicitly consume the eligible IDs; automatic reasoning integration is a separate change with a reviewed domain policy.
