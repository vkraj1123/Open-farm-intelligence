from abc import ABC, abstractmethod
from datetime import datetime

from ofi.domain.models import CropCycle, Farm, FarmSnapshot, LandParty, Observation, ProductionContract


class FarmTwinRepository(ABC):
    """Persistence boundary for the farm digital twin.

    Intelligence code depends on this interface, not on an in-memory store,
    PostgreSQL driver, or GIS implementation.
    """

    @abstractmethod
    def upsert(self, farm: Farm) -> Farm:
        raise NotImplementedError

    @abstractmethod
    def get(self, farm_id: str) -> Farm:
        raise NotImplementedError

    @abstractmethod
    def register_crop_cycle(self, farm_id: str, crop_cycle: CropCycle) -> None:
        raise NotImplementedError

    @abstractmethod
    def add_land_party(self, farm_id: str, party: LandParty) -> None:
        raise NotImplementedError

    @abstractmethod
    def add_contract(self, farm_id: str, contract: ProductionContract) -> None:
        raise NotImplementedError

    @abstractmethod
    def add_observation(self, farm_id: str, observation: Observation) -> None:
        raise NotImplementedError

    @abstractmethod
    def snapshot(self, farm_id: str, as_of: datetime | None = None) -> FarmSnapshot:
        raise NotImplementedError
