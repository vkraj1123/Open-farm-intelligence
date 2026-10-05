from datetime import datetime, timedelta, timezone

from ofi.domain.models import CropCycle, Farm, FarmCase, GeoPoint, Observation, Parcel
from ofi.intelligence.alignment import align, spatial_distance_km
from ofi.intelligence.fusion import conflict_flags, make_evidence


def test_fresh_high_quality_source_scores_higher():
    now = datetime.now(timezone.utc)
    base = Farm(id="f", farmer_id="u", parcel=Parcel(id="p", location=GeoPoint(latitude=27, longitude=72)), crop_cycle=CropCycle(crop="bajra"))
    case = FarmCase(id="c", farm=base, query="test", observations=[
        Observation(id="a", kind="soil", timestamp=now, value={}, source="soil_lab"),
        Observation(id="b", kind="soil", timestamp=now - timedelta(days=30), value={}, source="farmer_report"),
    ])
    scores = make_evidence(case)
    assert scores[0].score > scores[1].score


def test_conflict_flags_detect_material_source_disagreement():
    now = datetime.now(timezone.utc)
    base = Farm(id="f", farmer_id="u", parcel=Parcel(id="p", location=GeoPoint(latitude=27, longitude=72)), crop_cycle=CropCycle(crop="bajra"))
    case = FarmCase(id="c", farm=base, query="test", observations=[
        Observation(id="w1", kind="weather", timestamp=now, value={"temperature_c": 30}, source="weather_station"),
        Observation(id="w2", kind="weather", timestamp=now, value={"temperature_c": 42}, source="weather_model"),
    ])
    assert "weather.temperature_c:source_disagreement" in conflict_flags(case)


def test_alignment_rejects_stale_observations():
    now = datetime.now(timezone.utc)
    a = Observation(id="a", kind="weather", timestamp=now, value={"temperature_c": 30}, source="weather_station")
    b = Observation(id="b", kind="weather", timestamp=now - timedelta(days=10), value={"temperature_c": 42}, source="weather_model")
    result = align(a, b, time_window_hours=72)
    assert result.usable is False
    assert "temporal_mismatch" in result.reasons


def test_alignment_rejects_spatially_distant_observations():
    a = Observation(id="a", kind="soil", timestamp=datetime.now(timezone.utc),
                    value={"moisture_pct": 20}, source="soil_lab",
                    location=GeoPoint(latitude=27.0, longitude=72.0))
    b = Observation(id="b", kind="soil", timestamp=datetime.now(timezone.utc),
                    value={"moisture_pct": 30}, source="soil_sensor",
                    location=GeoPoint(latitude=27.2, longitude=72.0))
    result = align(a, b, max_distance_km=5)
    assert result.usable is False
    assert "spatial_mismatch" in result.reasons
