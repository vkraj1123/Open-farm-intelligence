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
    def get(self, farm_id: str, tenant_id: str | None = None) -> Farm:
        """Return a farm, optionally constraining lookup to one tenant."""
        raise NotImplementedError

    @abstractmethod
    def register_crop_cycle(
        self,
        farm_id: str,
        crop_cycle: CropCycle,
        *,
        tenant_id: str | None = None,
    ) -> None:
        """Mutate farm data, optionally requiring the farm to match this tenant."""
        raise NotImplementedError

    @abstractmethod
    def add_land_party(
        self,
        farm_id: str,
        party: LandParty,
        *,
        tenant_id: str | None = None,
    ) -> None:
        """Mutate farm data, optionally requiring the farm to match this tenant."""
        raise NotImplementedError

    @abstractmethod
    def add_contract(
        self,
        farm_id: str,
        contract: ProductionContract,
        *,
        tenant_id: str | None = None,
    ) -> None:
        """Mutate farm data, optionally requiring the farm to match this tenant."""
        raise NotImplementedError

    @abstractmethod
    def add_observation(
        self,
        farm_id: str,
        observation: Observation,
        *,
        tenant_id: str | None = None,
    ) -> None:
        """Mutate farm data, optionally requiring the farm to match this tenant."""
        raise NotImplementedError

    @abstractmethod
    def snapshot(
        self,
        farm_id: str,
        as_of: datetime | None = None,
        *,
        tenant_id: str | None = None,
    ) -> FarmSnapshot:
        """Return a snapshot, optionally requiring the farm's tenant to match."""
        raise NotImplementedError
