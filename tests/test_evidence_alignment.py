from datetime import date, datetime, timedelta, timezone

import pytest

from ofi.domain.models import CropCycle, FarmSnapshot, GeoPoint, Observation
from ofi.services.evidence_alignment import align_observations


AS_OF = datetime(2026, 10, 10, 12, 0, tzinfo=timezone.utc)


def snapshot():
    return FarmSnapshot(
        farm_id="farm-1",
        as_of=AS_OF,
        active_crop=CropCycle(
            id="crop-kharif-2026",
            crop="bajra",
            season="kharif",
            sowing_date=date(2026, 7, 1),
            harvest_date=date(2026, 10, 15),
        ),
        parcel_location=GeoPoint(latitude=27.0, longitude=72.0),
    )


def observation(
    observation_id,
    *,
    kind="weather",
    timestamp=AS_OF - timedelta(hours=1),
    crop_cycle_id="crop-kharif-2026",
    provenance=None,
):
    return Observation(
        id=observation_id,
        kind=kind,
        timestamp=timestamp,
        value={"temperature_c": 32},
        source="test-source",
        quality=0.8,
        confidence=0.7,
        crop_cycle_id=crop_cycle_id,
        provenance=provenance or {},
    )


def policies(**overrides):
    return {"weather": timedelta(days=1), **overrides}


def by_id(frame, observation_id):
    return next(item for item in frame.assessments if item.observation_id == observation_id)


def test_fresh_observation_is_eligible_and_preserves_quality_fields():
    item = observation("fresh-1", provenance={"record_id": "source-123"})

    frame = align_observations(snapshot(), [item], max_age_by_kind=policies())

    assessed = by_id(frame, "fresh-1")
    assert frame.farm_id == "farm-1"
    assert frame.crop_cycle_id == "crop-kharif-2026"
    assert frame.as_of == AS_OF
    assert assessed.freshness == "fresh"
    assert assessed.eligible is True
    assert assessed.quality == 0.8
    assert assessed.confidence == 0.7
    assert assessed.provenance_present is True
    assert frame.eligible_observation_ids == ("fresh-1",)


def test_stale_future_and_unconfigured_observations_are_excluded():
    observations = [
        observation("stale", timestamp=AS_OF - timedelta(days=3)),
        observation("future", timestamp=AS_OF + timedelta(minutes=1)),
        observation("unconfigured", kind="sensor"),
    ]

    frame = align_observations(snapshot(), observations, max_age_by_kind=policies())

    assert by_id(frame, "stale").freshness == "stale"
    assert by_id(frame, "stale").eligible is False
    assert by_id(frame, "future").freshness == "future"
    assert by_id(frame, "future").eligible is False
    assert by_id(frame, "unconfigured").freshness == "unconfigured"
    assert by_id(frame, "unconfigured").eligible is False
    assert frame.eligible_observation_ids == ()


def test_wrong_crop_cycle_and_duplicate_ids_are_excluded():
    observations = [
        observation("wrong-cycle", crop_cycle_id="crop-rabi-2026"),
        observation("duplicate", provenance={"record_id": "a"}),
        observation("duplicate", provenance={"record_id": "b"}),
    ]

    frame = align_observations(snapshot(), observations, max_age_by_kind=policies())

    assert "crop_cycle_mismatch" in by_id(frame, "wrong-cycle").issues
    assert by_id(frame, "wrong-cycle").eligible is False
    assert all(not item.eligible for item in frame.assessments if item.observation_id == "duplicate")
    assert frame.excluded_counts["duplicate_observation_id"] == 2
    assert frame.eligible_observation_ids == ()


def test_missing_provenance_is_reported_but_only_required_by_policy_when_configured():
    item = observation("no-provenance")

    permissive = align_observations(snapshot(), [item], max_age_by_kind=policies())
    strict = align_observations(
        snapshot(),
        [item],
        max_age_by_kind=policies(),
        require_provenance=True,
    )

    assert "provenance_missing" in by_id(permissive, "no-provenance").issues
    assert by_id(permissive, "no-provenance").eligible is True
    assert "provenance_required" in by_id(strict, "no-provenance").issues
    assert by_id(strict, "no-provenance").eligible is False


@pytest.mark.parametrize("window", [timedelta(0), timedelta(seconds=-1)])
def test_non_positive_freshness_window_is_rejected(window):
    with pytest.raises(ValueError, match="must be positive"):
        align_observations(snapshot(), [], max_age_by_kind={"weather": window})


def test_results_are_sorted_newest_first_deterministically():
    observations = [
        observation("older", timestamp=AS_OF - timedelta(hours=3)),
        observation("newer", timestamp=AS_OF - timedelta(hours=1)),
    ]

    frame = align_observations(snapshot(), observations, max_age_by_kind=policies())

    assert [item.observation_id for item in frame.assessments] == ["newer", "older"]
