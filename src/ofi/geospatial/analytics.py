from datetime import date, datetime, timezone
from statistics import mean
from typing import Iterable

from ofi.domain.models import CropCycle, FarmSnapshot, Observation
from ofi.geospatial.spatial import ParcelRasterSummary, RasterPixel, aggregate_pixels_to_parcel


def ndvi_trend(observations: Iterable[Observation]) -> float | None:
    points = sorted(
        ((obs.timestamp, float(obs.value["ndvi"])) for obs in observations
         if obs.kind == "satellite" and "ndvi" in obs.value),
        key=lambda item: item[0],
    )
    if len(points) < 2:
        return None
    start = points[0][0]
    xs = [(timestamp - start).total_seconds() / 86400.0 for timestamp, _ in points]
    ys = [value for _, value in points]
    x_mean, y_mean = mean(xs), mean(ys)
    denominator = sum((x - x_mean) ** 2 for x in xs)
    if denominator == 0:
        return 0.0
    return sum((x - x_mean) * (y - y_mean) for x, y in zip(xs, ys)) / denominator


def vegetation_stress_index(observations: Iterable[Observation]) -> float | None:
    recent = sorted(
        (obs for obs in observations if obs.kind == "satellite"
         and ("ndvi" in obs.value or "ndwi" in obs.value)),
        key=lambda item: item.timestamp, reverse=True,
    )
    if not recent:
        return None
    latest = recent[0].value
    signals = []
    if "ndvi" in latest:
        signals.append(max(0.0, min(1.0, (0.65 - float(latest["ndvi"])) / 0.65)))
    if "ndwi" in latest:
        signals.append(max(0.0, min(1.0, (-float(latest["ndwi"])) / 0.5)))
    return round(mean(signals), 3) if signals else None


def crop_age_days(crop_cycle: CropCycle, as_of: date) -> int | None:
    if crop_cycle.sowing_date is None or as_of < crop_cycle.sowing_date:
        return None
    return (as_of - crop_cycle.sowing_date).days


def derived_ndvi_observation(snapshot: FarmSnapshot) -> Observation | None:
    trend = ndvi_trend(snapshot.recent_observations)
    if trend is None:
        return None
    source_ids = [
        obs.id for obs in snapshot.recent_observations
        if obs.kind == "satellite" and "ndvi" in obs.value
    ]
    return Observation(
        id=f"ofi-derived-ndvi-trend-{snapshot.farm_id}-{int(snapshot.as_of.timestamp())}",
        kind="satellite",
        timestamp=snapshot.as_of,
        source="ofi_geospatial_analytics",
        value={"ndvi_trend": trend, "derived_from": source_ids},
        quality=0.8,
        confidence=0.75,
        crop_cycle_id=snapshot.active_crop.id,
        location=snapshot.parcel_location,
        spatial_scope="farm",
        provenance={"derived_from": source_ids, "method": "linear_ndvi_trend"},
    )


def select_recent_satellite_observation(
    observations: Iterable[Observation],
    *,
    as_of: datetime | None = None,
    max_age_days: float = 14.0,
) -> Observation | None:
    """Select the newest usable satellite observation within a freshness window."""
    moment = (as_of or datetime.now(timezone.utc)).astimezone(timezone.utc)
    candidates = [
        obs for obs in observations
        if obs.kind == "satellite"
        and "ndvi" in obs.value
        and obs.timestamp <= moment
        and (moment - obs.timestamp).total_seconds() <= max_age_days * 86400
    ]
    return max(candidates, key=lambda obs: obs.timestamp, default=None)


def parcel_ndvi_observation(
    snapshot: FarmSnapshot,
    pixels: Iterable[RasterPixel],
    *,
    scene_id: str,
    acquisition_time: datetime,
    source: str = "sentinel-2",
    source_id: str | None = None,
    cloud_cover_pct: float | None = None,
    quality: float = 1.0,
) -> Observation:
    """Create traceable parcel-level NDVI evidence from raw pixel candidates."""
    summary: ParcelRasterSummary = aggregate_pixels_to_parcel(
        _snapshot_parcel(snapshot), pixels
    )
    if summary.mean is None:
        raise ValueError("no valid NDVI pixels inside parcel")

    coverage_quality = summary.valid_pixel_fraction
    cloud_quality = 1.0 - summary.cloud_fraction
    effective_quality = max(0.0, min(1.0, quality * coverage_quality * cloud_quality))

    return Observation(
        id=f"{source}-parcel-ndvi-{snapshot.farm_id}-{int(acquisition_time.timestamp())}",
        kind="satellite",
        timestamp=acquisition_time.astimezone(timezone.utc),
        source=source,
        value={
            "ndvi": summary.mean,
            "mean_ndvi": summary.mean,
            "median_ndvi": summary.median,
            "valid_pixel_fraction": summary.valid_pixel_fraction,
            "cloud_fraction": summary.cloud_fraction,
            "pixel_count": summary.pixel_count,
        },
        quality=round(effective_quality, 3),
        confidence=round(coverage_quality, 3),
        location=snapshot.parcel_location,
        crop_cycle_id=snapshot.active_crop.id,
        spatial_scope="parcel",
        provenance={
            "scene_id": scene_id,
            "source_id": source_id,
            "acquisition_time": acquisition_time.astimezone(timezone.utc).isoformat(),
            "cloud_cover_pct": cloud_cover_pct,
            "aggregation": "parcel_pixel_mean_median",
            "valid_pixel_fraction": summary.valid_pixel_fraction,
            "cloud_fraction": summary.cloud_fraction,
        },
    )


def _snapshot_parcel(snapshot: FarmSnapshot):
    from ofi.domain.models import Parcel
    return Parcel(
        id=f"{snapshot.farm_id}-parcel",
        location=snapshot.parcel_location,
        boundary=snapshot.parcel_boundary,
    )
