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

The alignment report itself remains deterministic, in-memory and request-scoped. It does not persist assessments or provide source-specific policy versioning. The separate, opt-in comparison service described below compares pairs only when caller-declared unit, metric, spatial scope, time separation and crop-cycle rules pass. The opt-in `POST /cases/{case_id}/reason-with-evidence-policy` endpoint connects eligibility filtering to reasoning; callers must explicitly choose that endpoint and supply a reviewed policy.

## Evidence-gated reasoning

The opt-in endpoint `POST /cases/{case_id}/reason-with-evidence-policy` connects the alignment boundary to the existing orchestrator. It requires the same explicit positive freshness windows as the evidence-frame endpoint and accepts `require_provenance`. The case manager assesses the case's observations against a farm snapshot, creates a deep copy of the case containing only eligible observations, and sends that copy to the orchestrator. The original observation history remains intact.

The response contains both the updated case record and the exact evidence frame used for filtering, including per-observation issue codes and exclusion counts. The ordinary `POST /cases/{case_id}/reason` path is unchanged for backward compatibility; callers must deliberately choose the policy-gated endpoint. The policy is request-scoped and is not persisted or versioned by this change.

This is an eligibility gate, not a truth detector. It does not compare unlike metrics, resolve conflicting values, verify provider authority, or establish calibrated confidence. If all observations are excluded, the orchestrator receives an empty observation list and should use its existing insufficient-evidence behavior.


## Explicit pairwise evidence comparisons

The endpoint `POST /farms/{farm_id}/evidence-comparisons` accepts a freshness policy plus one or more caller-authored comparison rules. The response includes the eligibility assessment and pairwise comparison results. It does not mutate the farm twin or trigger a recommendation.

Each rule declares:
- `value_key`: one exact top-level numeric field in each observation's `value` object;
- `observation_kinds`: the observation kinds eligible to participate;
- `unit`: the exact required `Observation.unit` value for both observations;
- `spatial_scope`: the exact required non-empty `Observation.spatial_scope` value;
- `max_time_delta_seconds`: the maximum absolute timestamp separation;
- `absolute_tolerance`: the declared difference at or below which values are labelled `agree`;
- `require_same_crop_cycle` and `require_distinct_sources`: explicit scope/source gates, both true by default.

Only observations eligible in the accompanying evidence frame can produce an `agree` or `disagree` result. Pairs failing a gate are returned as `not_comparable` with reason codes. Missing, non-numeric and non-finite values are not compared. The service uses absolute differences; it does not normalize units, infer field equivalence, apply percentage tolerances, or decide whether a disagreement is agronomically important.

Example request (illustrative policy only):

```json
{
  "max_age_seconds_by_kind": {"weather": 3600},
  "require_provenance": true,
  "comparison_rules": [{
    "rule_id": "temperature-check-v1",
    "value_key": "temperature_c",
    "observation_kinds": ["weather"],
    "unit": "degC",
    "spatial_scope": "farm",
    "max_time_delta_seconds": 7200,
    "absolute_tolerance": 2.0,
    "require_same_crop_cycle": true,
    "require_distinct_sources": true
  }]
}
```

The values above demonstrate the request shape; they are not recommended production thresholds. A result of `disagree` means only that a configured rule was exceeded after comparability gates passed. It does not establish which source is correct or prove an agronomic contradiction. Rule governance, versioning and validation against authoritative agricultural data remain outstanding.
