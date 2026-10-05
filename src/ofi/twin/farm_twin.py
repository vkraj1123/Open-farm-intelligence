from datetime import date, datetime, timezone

from ofi.domain.models import (
    CropCycle,
    Farm,
    FarmSnapshot,
    LandParty,
    Observation,
    ProductionContract,
)


class FarmTwinStore:
    """MVP temporal farm twin; persistence can move to PostGIS later."""

    def __init__(self):
        self._farms: dict[str, Farm] = {}
        self._crop_cycles: dict[str, list[CropCycle]] = {}
        self._parties: dict[str, list[LandParty]] = {}
        self._contracts: dict[str, list[ProductionContract]] = {}
        self._observations: dict[str, list[Observation]] = {}

    def upsert(self, farm: Farm) -> Farm:
        self._farms[farm.id] = farm
        self._crop_cycles[farm.id] = [farm.crop_cycle]
        self._parties[farm.id] = list(farm.parties)
        self._contracts[farm.id] = list(farm.contracts)
        self._observations.setdefault(farm.id, [])
        return farm

    def get(self, farm_id: str) -> Farm:
        try:
            return self._farms[farm_id]
        except KeyError as exc:
            raise KeyError(farm_id) from exc

    def register_crop_cycle(self, farm_id: str, crop_cycle: CropCycle) -> None:
        self.get(farm_id)
        cycles = self._crop_cycles[farm_id]
        if any(item.id == crop_cycle.id for item in cycles):
            raise ValueError(f"crop cycle already registered: {crop_cycle.id}")
        cycles.append(crop_cycle)

    def add_land_party(self, farm_id: str, party: LandParty) -> None:
        self.get(farm_id)
        self._parties[farm_id].append(party)

    def add_contract(self, farm_id: str, contract: ProductionContract) -> None:
        self.get(farm_id)
        self._contracts[farm_id].append(contract)

    def add_observation(self, farm_id: str, observation: Observation) -> None:
        self.get(farm_id)
        self._observations[farm_id].append(observation)

    def snapshot(self, farm_id: str, as_of: datetime | None = None) -> FarmSnapshot:
        self.get(farm_id)
        moment = as_of or datetime.now(timezone.utc)
        moment = self._utc(moment)

        cycles = [
            cycle for cycle in self._crop_cycles[farm_id]
            if (cycle.sowing_date is None or cycle.sowing_date <= moment.date())
            and (cycle.harvest_date is None or cycle.harvest_date >= moment.date())
        ]
        if not cycles:
            raise ValueError(f"no active crop cycle for {farm_id} at {moment.date()}")
        active_crop = max(
            cycles,
            key=lambda item: item.sowing_date or date.min,
        )

        parties = [
            party for party in self._parties[farm_id]
            if party.valid_from <= moment.date()
            and (party.valid_to is None or party.valid_to >= moment.date())
        ]
        contracts = [
            contract for contract in self._contracts[farm_id]
            if contract.valid_from <= moment.date()
            and contract.valid_to >= moment.date()
        ]
        observations = [
            item for item in self._observations[farm_id]
            if item.timestamp <= moment
            and (item.crop_cycle_id is None or item.crop_cycle_id == active_crop.id)
        ]

        return FarmSnapshot(
            farm_id=farm_id,
            as_of=moment,
            active_crop=active_crop,
            parcel_location=self.get(farm_id).parcel.location,
            active_parties=parties,
            active_contracts=contracts,
            recent_observations=sorted(
                observations, key=lambda item: item.timestamp, reverse=True
            ),
        )

    @staticmethod
    def _utc(moment: datetime) -> datetime:
        if moment.tzinfo is None or moment.utcoffset() is None:
            raise ValueError("as_of must be timezone-aware")
        return moment.astimezone(timezone.utc)
