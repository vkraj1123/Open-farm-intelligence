from ofi.domain.models import CropCycle, FarmSnapshot, GeoPoint
from ofi.providers.adapters import AdapterError, SatelliteAdapter, satellite_scene


def snapshot():
    return FarmSnapshot(
        farm_id="f",
        as_of="2026-01-01T00:00:00Z",
        active_crop=CropCycle(id="c", crop="bajra"),
        parcel_location=GeoPoint(latitude=27.005, longitude=72.005),
        parcel_boundary=[
            GeoPoint(latitude=27.0, longitude=72.0),
            GeoPoint(latitude=27.0, longitude=72.01),
            GeoPoint(latitude=27.01, longitude=72.01),
            GeoPoint(latitude=27.01, longitude=72.0),
        ],
    )


def test_satellite_scene_parses_metadata():
    scene = satellite_scene({
        "scene": {
            "scene_id": "S2A_001",
            "acquisition_time": "2026-01-01T05:00:00Z",
            "tile_id": "43RGL",
            "cloud_cover_pct": 8,
            "footprint": [
                {"latitude": 27.0, "longitude": 72.0},
                {"latitude": 27.0, "longitude": 72.02},
                {"latitude": 27.02, "longitude": 72.02},
                {"latitude": 27.02, "longitude": 72.0},
            ],
        }
    })
    assert scene.scene_id == "S2A_001"
    assert scene.tile_id == "43RGL"
    assert scene.cloud_cover_pct == 8


def test_satellite_adapter_rejects_non_overlapping_scene():
    def fetch(_):
        return {
            "timestamp": "2026-01-01T05:00:00Z",
            "source": "sentinel-2",
            "source_id": "S2A_002",
            "ndvi": 0.4,
            "scene": {
                "scene_id": "S2A_002",
                "acquisition_time": "2026-01-01T05:00:00Z",
                "footprint": [
                    {"latitude": 28.0, "longitude": 73.0},
                    {"latitude": 28.0, "longitude": 73.01},
                    {"latitude": 28.01, "longitude": 73.01},
                ],
            },
        }

    try:
        SatelliteAdapter(fetch).collect(snapshot())
    except AdapterError as exc:
        assert "does not overlap" in str(exc)
    else:
        raise AssertionError("expected non-overlapping scene to be rejected")


def test_satellite_adapter_keeps_scene_provenance():
    def fetch(_):
        return {
            "timestamp": "2026-01-01T05:00:00Z",
            "source": "sentinel-2",
            "source_id": "S2A_003",
            "ndvi": 0.62,
            "scene": {
                "scene_id": "S2A_003",
                "acquisition_time": "2026-01-01T05:00:00Z",
                "tile_id": "43RGL",
                "cloud_cover_pct": 5,
                "footprint": [
                    {"latitude": 27.0, "longitude": 72.0},
                    {"latitude": 27.0, "longitude": 72.02},
                    {"latitude": 27.02, "longitude": 72.02},
                    {"latitude": 27.02, "longitude": 72.0},
                ],
            },
        }

    observation = SatelliteAdapter(fetch).collect(snapshot())[0]
    assert observation.value["ndvi"] == 0.62
    assert observation.provenance["scene_id"] == "S2A_003"
    assert observation.provenance["tile_id"] == "43RGL"
    assert observation.provenance["cloud_cover_pct"] == 5
    assert len(observation.provenance["footprint"]) == 4
