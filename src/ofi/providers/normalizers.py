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


def weather_observation(snapshot: FarmSnapshot, *, provider: str, payload: dict[str, Any]) -> Observation:
    timestamp = _timestamp(payload.get("timestamp"))
    return Observation(
        id=f"{provider}-weather-{snapshot.farm_id}-{int(timestamp.timestamp())}",
        kind="weather",
        timestamp=timestamp,
        source=payload.get("source", provider),
        value={key: payload[key] for key in (
            "rainfall_mm_next_3d", "rainfall_mm_last_7d", "temperature_c",
            "humidity_pct", "wind_speed_ms"
        ) if key in payload},
        quality=float(payload.get("quality", 1.0)),
        confidence=float(payload.get("confidence", 1.0)),
        crop_cycle_id=snapshot.active_crop.id,
        spatial_scope="farm",
        provenance={"provider": provider, "provider_source_id": payload.get("source_id")},
    )


def satellite_observation(snapshot: FarmSnapshot, *, provider: str, payload: dict[str, Any]) -> Observation:
    timestamp = _timestamp(payload.get("timestamp"))
    value = {key: payload[key] for key in (
        "ndvi", "ndvi_trend", "evi", "ndwi", "cloud_cover_pct"
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
        crop_cycle_id=snapshot.active_crop.id,
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
        crop_cycle_id=snapshot.active_crop.id,
    )
