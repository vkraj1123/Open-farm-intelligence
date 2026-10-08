from ofi.providers.base import ProviderRegistry
from ofi.providers.mock_evidence import MockSatelliteProvider, MockSoilProvider, MockWeatherProvider

def default_mock_registry() -> ProviderRegistry:
    return ProviderRegistry([
        MockWeatherProvider(),
        MockSatelliteProvider(),
        MockSoilProvider(),
    ])
