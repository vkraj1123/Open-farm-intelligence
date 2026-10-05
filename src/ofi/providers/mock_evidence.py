from ofi.domain.models import FarmSnapshot, Observation
from ofi.providers.base import EvidenceProvider
from ofi.providers.normalizers import satellite_observation, soil_observation, weather_observation


class MockWeatherProvider(EvidenceProvider):
    name = "mock_weather"
    capabilities = frozenset({"weather"})

    def collect(self, snapshot: FarmSnapshot) -> list[Observation]:
        return [weather_observation(snapshot, provider=self.name, payload={
            "source": "weather_model",
            "rainfall_mm_next_3d": 0.0,
            "rainfall_mm_last_7d": 4.0,
            "temperature_c": 34.0,
            "temperature_min_c": 25.0,\n            "temperature_max_c": 38.0,\n            "humidity_min_pct": 30.0,\n            "humidity_max_pct": 70.0,\n            "solar_mj_m2_day": 22.0,\n            "wind_speed_ms": 2.0,
            "humidity_pct": 31.0,
            "quality": 0.8,
            "confidence": 0.8,
        })]


class MockSatelliteProvider(EvidenceProvider):
    name = "mock_satellite"
    capabilities = frozenset({"satellite", "vegetation_index"})

    def collect(self, snapshot: FarmSnapshot) -> list[Observation]:
        return [satellite_observation(snapshot, provider=self.name, payload={
            "source": "satellite",
            "ndvi_trend": -0.08,
            "ndvi": 0.41,
            "ndwi": -0.18,
            "cloud_cover_pct": 8.0,
            "quality": 0.85,
            "confidence": 0.85,
        })]


class MockSoilProvider(EvidenceProvider):
    name = "mock_soil"
    capabilities = frozenset({"soil"})

    def collect(self, snapshot: FarmSnapshot) -> list[Observation]:
        return [soil_observation(snapshot, provider=self.name, payload={
            "source": "soil_lab",
            "moisture_pct": 19.0,
            "ph": 7.8,
            "organic_carbon_pct": 0.38,
            "quality": 0.75,
            "confidence": 0.7,
        })]
