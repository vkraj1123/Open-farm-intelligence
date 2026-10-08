from dataclasses import dataclass
from typing import Any, Mapping

from ofi.domain.models import FarmSnapshot, GeoPoint, Observation
from ofi.geospatial.spatial import SpatialFootprint, footprint_overlaps_parcel
from ofi.providers.base import EvidenceProvider
from ofi.providers.normalizers import satellite_observation, soil_observation, weather_observation


@dataclass(frozen=True)
class ProviderPayload:
    """Canonical envelope expected from real-data adapters."""

    timestamp: str | None
    source: str
    source_id: str | None
    values: Mapping[str, Any]
    unit: str | None = None
    spatial_scope: str = "farm"
    quality: float = 1.0
    confidence: float = 1.0


@dataclass(frozen=True)
class SatelliteScene:
    """Provider-neutral metadata for a satellite scene or analysis tile."""

    scene_id: str
    acquisition_time: str
    footprint: SpatialFootprint
    cloud_cover_pct: float | None = None
    tile_id: str | None = None


class AdapterError(ValueError):
    pass


class WeatherAdapter(EvidenceProvider):
    name = "weather_adapter"
    capabilities = frozenset({"weather"})

    def __init__(self, fetcher):
        self.fetcher = fetcher

    def collect(self, snapshot: FarmSnapshot) -> list[Observation]:
        payload = self.fetcher(snapshot)
        envelope = canonical_payload(payload, required=("temperature_c",))
        return [weather_observation(snapshot, provider=self.name, payload={
            **envelope.values, "timestamp": envelope.timestamp, "source": envelope.source,
            "source_id": envelope.source_id, "quality": envelope.quality,
            "confidence": envelope.confidence, "unit": envelope.unit,
            "spatial_scope": envelope.spatial_scope,
        })]


class SatelliteAdapter(EvidenceProvider):
    """Translate Sentinel-style scene summaries into parcel-scoped observations.

    The fetcher owns API authentication/query mechanics. OFI owns the canonical
    scene metadata, parcel-overlap validation, and observation provenance.
    """

    name = "satellite_adapter"
    capabilities = frozenset({"satellite", "vegetation_index", "remote_sensing"})

    def __init__(self, fetcher):
        self.fetcher = fetcher

    def collect(self, snapshot: FarmSnapshot) -> list[Observation]:
        payload = self.fetcher(snapshot)
        envelope = canonical_payload(payload, required=("ndvi",))
        scene = satellite_scene(payload)
        if scene is not None:
            parcel = _snapshot_parcel(snapshot)
            if not footprint_overlaps_parcel(parcel, scene.footprint):
                raise AdapterError("satellite scene footprint does not overlap farm parcel")
        observation_payload = {
            **envelope.values,
            "timestamp": envelope.timestamp,
            "source": envelope.source,
            "source_id": envelope.source_id,
            "quality": envelope.quality,
            "confidence": envelope.confidence,
            "scene_id": scene.scene_id if scene else envelope.source_id,
            "tile_id": scene.tile_id if scene else None,
            "acquisition_time": scene.acquisition_time if scene else envelope.timestamp,
            "cloud_cover_pct": (
                scene.cloud_cover_pct
                if scene and scene.cloud_cover_pct is not None
                else envelope.values.get("cloud_cover_pct")
            ),
            "spatial_scope": envelope.spatial_scope,
        }
        if scene:
            observation_payload["footprint"] = [
                {"latitude": p.latitude, "longitude": p.longitude}
                for p in scene.footprint.points
            ]
        return [satellite_observation(
            snapshot, provider=self.name, payload=observation_payload
        )]


class SoilAdapter(EvidenceProvider):
    name = "soil_adapter"
    capabilities = frozenset({"soil"})

    def __init__(self, fetcher):
        self.fetcher = fetcher

    def collect(self, snapshot: FarmSnapshot) -> list[Observation]:
        payload = self.fetcher(snapshot)
        envelope = canonical_payload(payload, required=("moisture_pct",))
        return [soil_observation(snapshot, provider=self.name, payload={
            **envelope.values, "timestamp": envelope.timestamp, "source": envelope.source,
            "source_id": envelope.source_id, "quality": envelope.quality,
            "confidence": envelope.confidence, "unit": envelope.unit,
            "spatial_scope": envelope.spatial_scope,
        })]


def satellite_scene(payload: Mapping[str, Any]) -> SatelliteScene | None:
    raw = payload.get("scene")
    if raw is None:
        return None
    if not isinstance(raw, Mapping):
        raise AdapterError("satellite scene must be a mapping")
    required = ("scene_id", "acquisition_time", "footprint")
    missing = [key for key in required if key not in raw]
    if missing:
        raise AdapterError(f"satellite scene missing required fields: {', '.join(missing)}")
    points = raw["footprint"]
    if not isinstance(points, (list, tuple)):
        raise AdapterError("satellite footprint must be a list")
    try:
        footprint = SpatialFootprint(tuple(
            GeoPoint(latitude=float(point["latitude"]), longitude=float(point["longitude"]))
            for point in points
        ))
    except (KeyError, TypeError, ValueError) as exc:
        raise AdapterError("satellite footprint contains invalid coordinates") from exc
    cloud = raw.get("cloud_cover_pct")
    if cloud is not None and not 0 <= float(cloud) <= 100:
        raise AdapterError("satellite cloud_cover_pct must be between 0 and 100")
    return SatelliteScene(
        scene_id=str(raw["scene_id"]),
        acquisition_time=str(raw["acquisition_time"]),
        footprint=footprint,
        cloud_cover_pct=float(cloud) if cloud is not None else None,
        tile_id=str(raw["tile_id"]) if raw.get("tile_id") is not None else None,
    )


def _snapshot_parcel(snapshot: FarmSnapshot):
    from ofi.domain.models import Parcel
    return Parcel(
        id=f"{snapshot.farm_id}-parcel",
        location=snapshot.parcel_location,
        boundary=snapshot.parcel_boundary,
    )


def canonical_payload(payload: Mapping[str, Any], *, required: tuple[str, ...]) -> ProviderPayload:
    if not isinstance(payload, Mapping):
        raise AdapterError("provider response must be a mapping")
    values = payload.get("values", payload)
    if not isinstance(values, Mapping):
        raise AdapterError("provider values must be a mapping")
    missing = [key for key in required if key not in values]
    if missing:
        raise AdapterError(f"provider response missing required fields: {', '.join(missing)}")
    quality = float(payload.get("quality", 1.0))
    confidence = float(payload.get("confidence", 1.0))
    if not 0 <= quality <= 1 or not 0 <= confidence <= 1:
        raise AdapterError("quality and confidence must be between 0 and 1")
    return ProviderPayload(
        timestamp=payload.get("timestamp"),
        source=str(payload.get("source", "external")),
        source_id=payload.get("source_id"),
        values=dict(values),
        unit=payload.get("unit"),
        spatial_scope=str(payload.get("spatial_scope", "farm")),
        quality=quality,
        confidence=confidence,
    )
