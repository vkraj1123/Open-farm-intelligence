from datetime import datetime, timezone

from ofi.domain.models import CropCycle, Farm, GeoPoint, Parcel
from ofi.twin.farm_twin import FarmTwinStore
from ofi.twin.repository import FarmTwinRepository


def test_in_memory_store_implements_repository_boundary():
    assert issubclass(FarmTwinStore, FarmTwinRepository)


def test_repository_boundary_preserves_snapshot_geometry():
    store = FarmTwinStore()
    store.upsert(Farm(
        id="f", farmer_id="u",
        parcel=Parcel(
            id="p",
            location=GeoPoint(latitude=27, longitude=72),
            boundary=[
                GeoPoint(latitude=27, longitude=72),
                GeoPoint(latitude=27, longitude=72.01),
                GeoPoint(latitude=27.01, longitude=72.01),
            ],
        ),
        crop_cycle=CropCycle(id="c", crop="bajra"),
    ))
    snapshot = store.snapshot("f", datetime.now(timezone.utc))
    assert len(snapshot.parcel_boundary) == 3
