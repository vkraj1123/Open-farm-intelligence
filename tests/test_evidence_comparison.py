from datetime import date, datetime, timedelta, timezone

import pytest

from ofi.domain.models import CropCycle, FarmSnapshot, GeoPoint, Observation
from ofi.services.evidence_alignment import align_observations
from ofi.services.evidence_comparison import (
    MetricComparisonRule,
    compare_aligned_observations,
)


AS_OF = datetime(2026, 10, 11, 10, 0, tzinfo=timezone.utc)


def make_snapshot():
    return FarmSnapshot(
        farm_id="farm-1",
        as_of=AS_OF,
        active_crop=CropCycle(
            id="crop-1",
            crop="bajra",
            sowing_date=date(2026, 7, 1),
            harvest_date=date(2026, 10, 15),
        ),
        parcel_location=GeoPoint(latitude=27.0, longitude=72.0),
    )


def make_observation(
    observation_id,
    value,
    *,
    source,
    timestamp=AS_OF - timedelta(minutes=30),
    unit="degC",
    spatial_scope="farm",
    crop_cycle_id="crop-1",
    kind="weather",
):
    return Observation(
        id=observation_id,
        kind=kind,
        timestamp=timestamp,
        value=value,
        source=source,
        quality=0.8,
        confidence=0.7,
        unit=unit,
        spatial_scope=spatial_scope,
        crop_cycle_id=crop_cycle_id,
        provenance={"synthetic": True},
    )


def rule(**overrides):
    values = {
        "rule_id": "air-temperature-check",
        "value_key": "temperature_c",
        "observation_kinds": ("weather",),
        "unit": "degC",
        "spatial_scope": "farm",
        "max_time_delta": timedelta(hours=2),
        "absolute_tolerance": 2.0,
        "require_same_crop_cycle": True,
        "require_distinct_sources": True,
    }
    values.update(overrides)
    return MetricComparisonRule(**values)


def assess(observations, comparison_rule=None, max_age=timedelta(days=1)):
    frame = align_observations(
        make_snapshot(),
        observations,
        max_age_by_kind={"weather": max_age},
        require_provenance=True,
    )
    return compare_aligned_observations(
        observations, frame, [comparison_rule or rule()]
    )


def test_compatible_measurements_within_absolute_tolerance_agree():
    observations = [
        make_observation("a", {"temperature_c": 30}, source="station-a"),
        make_observation("b", {"temperature_c": 31.5}, source="station-b"),
    ]
    report = assess(observations)
    comparison = report.comparisons[0]
    assert comparison.status == "agree"
    assert comparison.absolute_difference == 1.5
    assert comparison.reasons == ()


def test_measurements_exceeding_rule_tolerance_are_reported_as_disagree_not_truth():
    observations = [
        make_observation("a", {"temperature_c": 30}, source="station-a"),
        make_observation("b", {"temperature_c": 36}, source="station-b"),
    ]
    comparison = assess(observations).comparisons[0]
    assert comparison.status == "disagree"
    assert comparison.absolute_difference == 6
    assert comparison.reasons == ("difference_exceeds_absolute_tolerance",)


@pytest.mark.parametrize(
    ("left_options", "right_options", "rule_options", "reason"),
    [
        ({}, {"unit": "F"}, {}, "unit_mismatch_or_missing"),
        ({}, {"spatial_scope": "parcel"}, {}, "spatial_scope_mismatch_or_missing"),
        ({}, {"timestamp": AS_OF - timedelta(hours=5)}, {}, "time_separation_exceeds_policy"),
        ({}, {"crop_cycle_id": "crop-2"}, {}, "crop_cycle_scope_missing_or_mismatch"),
        ({}, {"source": "station-a"}, {}, "same_source"),
        ({}, {"value": {"temperature_c": "hot"}}, {}, "right_value_missing_or_non_numeric"),
    ],
)
def test_non_comparable_pairs_are_reported_with_explicit_reasons(
    left_options, right_options, rule_options, reason
):
    left_arguments = {"value": {"temperature_c": 30}, "source": "station-a"}
    left_arguments.update(left_options)
    right_arguments = {"value": {"temperature_c": 36}, "source": "station-b"}
    right_arguments.update(right_options)
    left = make_observation("a", **left_arguments)
    right = make_observation("b", **right_arguments)
    comparison = assess([left, right], rule(**rule_options)).comparisons[0]
    assert comparison.status == "not_comparable"
    assert reason in comparison.reasons
    assert comparison.absolute_difference is None


def test_stale_observation_never_drives_a_value_disagreement():
    observations = [
        make_observation("a", {"temperature_c": 30}, source="station-a"),
        make_observation(
            "b", {"temperature_c": 40}, source="station-b",
            timestamp=AS_OF - timedelta(days=4),
        ),
    ]
    comparison = assess(observations, max_age=timedelta(days=1)).comparisons[0]
    assert comparison.status == "not_comparable"
    assert "observation_not_eligible" in comparison.reasons


def test_missing_metric_and_non_finite_values_are_not_comparable():
    observations = [
        make_observation("a", {"other_metric": 30}, source="station-a"),
        make_observation("b", {"temperature_c": float("inf")}, source="station-b"),
    ]
    comparison = assess(observations).comparisons[0]
    assert comparison.status == "not_comparable"
    assert "left_value_missing_or_non_numeric" in comparison.reasons
    assert "right_value_missing_or_non_numeric" in comparison.reasons


def test_duplicate_rule_ids_and_invalid_tolerances_are_rejected():
    observations = [
        make_observation("a", {"temperature_c": 30}, source="station-a"),
        make_observation("b", {"temperature_c": 31}, source="station-b"),
    ]
    frame = align_observations(
        make_snapshot(), observations, max_age_by_kind={"weather": timedelta(days=1)}
    )
    with pytest.raises(ValueError, match="rule IDs must be unique"):
        compare_aligned_observations(observations, frame, [rule(), rule()])
    with pytest.raises(ValueError, match="absolute_tolerance"):
        rule(absolute_tolerance=float("nan"))
    with pytest.raises(ValueError, match="max_time_delta"):
        rule(max_time_delta=timedelta(0))


def test_output_order_is_deterministic():
    observations = [
        make_observation("z", {"temperature_c": 30}, source="station-z"),
        make_observation("a", {"temperature_c": 30}, source="station-a"),
        make_observation("m", {"temperature_c": 30}, source="station-m"),
    ]
    report1 = assess(observations)
    report2 = assess(list(reversed(observations)))
    projection = lambda report: [
        (item.rule_id, item.left_observation_id, item.right_observation_id, item.status)
        for item in report.comparisons
    ]
    assert projection(report1) == projection(report2)


def test_non_finite_difference_is_not_serialized_as_a_disagreement():
    observations = [
        make_observation("a", {"temperature_c": 1e308}, source="station-a"),
        make_observation("b", {"temperature_c": -1e308}, source="station-b"),
    ]
    comparison = assess(observations).comparisons[0]
    assert comparison.status == "not_comparable"
    assert comparison.absolute_difference is None
    assert "absolute_difference_non_finite" in comparison.reasons
