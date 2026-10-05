from datetime import date
from statistics import mean
from typing import Iterable

from ofi.domain.models import CropCycle, FarmSnapshot, Observation


def ndvi_trend(observations: Iterable[Observation]) -> float | None:
    """Estimate NDVI slope per day; screening indicator, not a crop-growth model."""
    points = sorted(
        (
            (obs.timestamp, float(obs.value["ndvi"]))
            for obs in observations
            if obs.kind == "satellite" and "ndvi" in obs.value
        ),
        key=lambda item: item[0],
    )
    if len(points) < 2:
        return None

    start = points[0][0]
    xs = [(timestamp - start).total_seconds() / 86400.0 for timestamp, _ in points]
    ys = [value for _, value in points]
    x_mean = mean(xs)
    y_mean = mean(ys)
    denominator = sum((x - x_mean) ** 2 for x in xs)
    if denominator == 0:
        return 0.0
    return sum((x - x_mean) * (y - y_mean) for x, y in zip(xs, ys)) / denominator


def vegetation_stress_index(observations: Iterable[Observation]) -> float | None:
    recent = sorted(
        (
            obs for obs in observations
            if obs.kind == "satellite"
            and ("ndvi" in obs.value or "ndwi" in obs.value)
        ),
        key=lambda item: item.timestamp,
        reverse=True,
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
    return Observation(
        id=f"ofi-derived-ndvi-trend-{snapshot.farm_id}-{int(snapshot.as_of.timestamp())}",
        kind="satellite",
        timestamp=snapshot.as_of,
        source="ofi_geospatial_analytics",
        value={
            "ndvi_trend": trend,
            "derived_from": [
                obs.id for obs in snapshot.recent_observations
                if obs.kind == "satellite" and "ndvi" in obs.value
            ],
        },
        quality=0.8,
        confidence=0.75,
        crop_cycle_id=snapshot.active_crop.id,
    )
