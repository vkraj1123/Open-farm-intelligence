from datetime import date

from ofi.domain.models import GeoPoint
from ofi.twin.postgis import PostGISFarmTwinStore
from ofi.twin.repository import FarmTwinRepository


def test_postgis_store_implements_repository_boundary():
    assert issubclass(PostGISFarmTwinStore, FarmTwinRepository)


def test_postgis_polygon_wkt_closes_ring():
    points = [
        GeoPoint(latitude=27.0, longitude=72.0),
        GeoPoint(latitude=27.0, longitude=72.01),
        GeoPoint(latitude=27.01, longitude=72.01),
    ]
    assert PostGISFarmTwinStore._polygon_wkt(points) == (
        "SRID=4326;POLYGON((72.0 27.0, 72.01 27.0, 72.01 27.01, 72.0 27.0))"
    )


def test_postgis_polygon_rejects_incomplete_geometry():
    points = [GeoPoint(latitude=27.0, longitude=72.0)]
    assert PostGISFarmTwinStore._polygon_wkt(points) is None
