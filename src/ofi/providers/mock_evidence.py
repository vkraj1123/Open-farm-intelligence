from datetime import datetime, timezone

from ofi.domain.models import FarmSnapshot, Observation
from ofi.providers.base import EvidenceProvider


class MockWeatherProvider(EvidenceProvider):
    name = "mock_weather"

    def collect(self, snapshot: FarmSnapshot) -> list[Observation]:
        return [Observation(
            id=f"{self.name}-{snapshot.farm_id}",
            kind="weather",
            timestamp=datetime.now(timezone.utc),
            value={"rainfall_mm_next_3d": 0.0, "temperature_c": 34.0},
            source="weather_model",
            quality=0.8,
            confidence=0.8,
            location=snapshot.active_parties[0].__class__ and None,
            crop_cycle_id=snapshot.active_crop.id,
        )]


class MockSatelliteProvider(EvidenceProvider):
    name = "mock_satellite"

    def collect(self, snapshot: FarmSnapshot) -> list[Observation]:
        return [Observation(
            id=f"{self.name}-{snapshot.farm_id}",
            kind="satellite",
            timestamp=datetime.now(timezone.utc),
            value={"ndvi_trend": -0.08, "ndvi": 0.41},
            source="satellite",
            quality=0.85,
            confidence=0.85,
            crop_cycle_id=snapshot.active_crop.id,
        )]


class MockSoilProvider(EvidenceProvider):
    name = "mock_soil"

    def collect(self, snapshot: FarmSnapshot) -> list[Observation]:
        return [Observation(
            id=f"{self.name}-{snapshot.farm_id}",
            kind="soil",
            timestamp=datetime.now(timezone.utc),
            value={"moisture_pct": 19.0, "ph": 7.8},
            source="soil_lab",
            quality=0.75,
            confidence=0.7,
            crop_cycle_id=snapshot.active_crop.id,
        )]
