from dataclasses import dataclass
from typing import Any, Mapping

from ofi.domain.models import FarmSnapshot, Observation
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


class AdapterError(ValueError):
    pass


class WeatherAdapter(EvidenceProvider):
    """Translate a weather service payload into canonical OFI observations."""

    name = "weather_adapter"
    capabilities = frozenset({"weather"})

    def __init__(self, fetcher):
        self.fetcher = fetcher

    def collect(self, snapshot: FarmSnapshot) -> list[Observation]:
        payload = self.fetcher(snapshot)
        envelope = canonical_payload(payload, required=("temperature_c",))
        return [weather_observation(snapshot, provider=self.name, payload={
            **envelope.values,
            "timestamp": envelope.timestamp,
            "source": envelope.source,
            "source_id": envelope.source_id,
            "quality": envelope.quality,
            "confidence": envelope.confidence,
        })]


class SatelliteAdapter(EvidenceProvider):
    """Translate Sentinel/OpenFarm-style vegetation payloads into OFI observations."""

    name = "satellite_adapter"
    capabilities = frozenset({"satellite", "vegetation_index"})

    def __init__(self, fetcher):
        self.fetcher = fetcher

    def collect(self, snapshot: FarmSnapshot) -> list[Observation]:
        payload = self.fetcher(snapshot)
        envelope = canonical_payload(payload, required=("ndvi",))
        return [satellite_observation(snapshot, provider=self.name, payload={
            **envelope.values,
            "timestamp": envelope.timestamp,
            "source": envelope.source,
            "source_id": envelope.source_id,
            "quality": envelope.quality,
            "confidence": envelope.confidence,
        })]


class SoilAdapter(EvidenceProvider):
    """Translate laboratory/sensor soil payloads into OFI observations."""

    name = "soil_adapter"
    capabilities = frozenset({"soil"})

    def __init__(self, fetcher):
        self.fetcher = fetcher

    def collect(self, snapshot: FarmSnapshot) -> list[Observation]:
        payload = self.fetcher(snapshot)
        envelope = canonical_payload(payload, required=("moisture_pct",))
        return [soil_observation(snapshot, provider=self.name, payload={
            **envelope.values,
            "timestamp": envelope.timestamp,
            "source": envelope.source,
            "source_id": envelope.source_id,
            "quality": envelope.quality,
            "confidence": envelope.confidence,
        })]


def canonical_payload(payload: Mapping[str, Any], *, required: tuple[str, ...]) -> ProviderPayload:
    if not isinstance(payload, Mapping):
        raise AdapterError("provider response must be a mapping")

    missing = [key for key in required if key not in payload]
    if missing:
        raise AdapterError(f"provider response missing required fields: {', '.join(missing)}")

    quality = float(payload.get("quality", 1.0))
    confidence = float(payload.get("confidence", 1.0))
    if not 0 <= quality <= 1 or not 0 <= confidence <= 1:
        raise AdapterError("quality and confidence must be between 0 and 1")

    values = payload.get("values", payload)
    if not isinstance(values, Mapping):
        raise AdapterError("provider values must be a mapping")

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
