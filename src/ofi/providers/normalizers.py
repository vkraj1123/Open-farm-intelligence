from datetime import datetime, timezone
from typing import Any

from ofi.domain.models import FarmSnapshot, Observation


def _timestamp(value: str | datetime | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("provider timestamp must be timezone-aware")
    return value.astimezone(timezone.utc)


def _provenance(provider: str, payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "provider": provider,
        "provider_source_id": payload.get("source_id"),
        **({"scene_id": payload["scene_id"]} if "scene_id" in payload else {}),
    }


def weather_observation(snapshot: FarmSnapshot, *, provider: str, payload: dict[str, Any]) -> Observation:
    timestamp = _timestamp(payload.get("timestamp"))
    supported = (
        "rainfall_mm_next_3d", "rainfall_mm_last_7d", "temperature_c",
        "temperature_min_c", "temperature_max_c", "humidity_pct",
        "humidity_min_pct", "humidity_max_pct", "solar_mj_m2_day",
        "wind_speed_ms",
    )
    return Observation(
        id=f"{provider}-weather-{snapshot.farm_id}-{int(timestamp.timestamp())}",
        kind="weather",
        timestamp=timestamp,
        source=payload.get("source", provider),
        value={key: payload[key] for key in supported if key in payload},
        quality=float(payload.get("quality", 1.0)),
        confidence=float(payload.get("confidence", 1.0)),
        location=snapshot.parcel_location,
        crop_cycle_id=snapshot.active_crop.id,
        unit=payload.get("unit"),
        spatial_scope=payload.get("spatial_scope", "farm"),
        provenance=_provenance(provider, payload),
    )


def satellite_observation(snapshot: FarmSnapshot, *, provider: str, payload: dict[str, Any]) -> Observation:
    timestamp = _timestamp(payload.get("timestamp"))
    value = {key: payload[key] for key in (
        "ndvi", "ndvi_trend", "evi", "ndwi", "cloud_cover_pct",
        "mean_ndvi", "median_ndvi", "valid_pixel_fraction",
    ) if key in payload}
    if not value:
        raise ValueError("satellite payload contains no supported vegetation indices")
    return Observation(
        id=f"{provider}-satellite-{snapshot.farm_id}-{int(timestamp.timestamp())}",
        kind="satellite",
        timestamp=timestamp,
        source=payload.get("source", provider),
        value=value,
        quality=float(payload.get("quality", 1.0)),
        confidence=float(payload.get("confidence", 1.0)),
        location=snapshot.parcel_location,
        crop_cycle_id=snapshot.active_crop.id,
        spatial_scope=payload.get("spatial_scope", "farm"),
        provenance=_provenance(provider, payload),
    )


def soil_observation(snapshot: FarmSnapshot, *, provider: str, payload: dict[str, Any]) -> Observation:
    timestamp = _timestamp(payload.get("timestamp"))
    value = {key: payload[key] for key in (
        "moisture_pct", "ph", "organic_carbon_pct",
        "available_n_pct", "available_p_kg_ha", "available_k_kg_ha"
    ) if key in payload}
    if not value:
        raise ValueError("soil payload contains no supported soil measurements")
    return Observation(
        id=f"{provider}-soil-{snapshot.farm_id}-{int(timestamp.timestamp())}",
        kind="soil",
        timestamp=timestamp,
        source=payload.get("source", provider),
        value=value,
        quality=float(payload.get("quality", 1.0)),
        confidence=float(payload.get("confidence", 1.0)),
        location=snapshot.parcel_location,
        crop_cycle_id=snapshot.active_crop.id,
        spatial_scope=payload.get("spatial_scope", "farm"),
        provenance=_provenance(provider, payload),
    )
