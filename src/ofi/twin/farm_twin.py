from datetime import datetime, timezone
from ofi.domain.models import Farm, FarmSnapshot, LandParty, Observation

class FarmTwinStore:
    """MVP farm digital twin; persistence will move to PostGIS."""
    def __init__(self):
        self._farms: dict[str, Farm] = {}
        self._observations: dict[str, list[Observation]] = {}

    def upsert(self, farm: Farm) -> Farm:
        self._farms[farm.id] = farm
        self._observations.setdefault(farm.id, [])
        return farm

    def get(self, farm_id: str) -> Farm:
        if farm_id not in self._farms:
            raise KeyError(farm_id)
        return self._farms[farm_id]

    def add_observation(self, farm_id: str, observation: Observation) -> None:
        self.get(farm_id)
        self._observations[farm_id].append(observation)

    def snapshot(self, farm_id: str, as_of: datetime | None = None) -> FarmSnapshot:
        farm = self.get(farm_id)
        moment = as_of or datetime.now(timezone.utc)
        observations = [
            item for item in self._observations[farm_id]
            if item.timestamp <= moment and (
                item.crop_cycle_id is None or item.crop_cycle_id == farm.crop_cycle.id
            )
        ]
        parties = [
            party for party in farm.parties
            if party.valid_from <= moment.date()
            and (party.valid_to is None or party.valid_to >= moment.date())
        ]
        return FarmSnapshot(
            farm_id=farm.id,
            as_of=moment,
            active_crop=farm.crop_cycle,
            active_parties=parties,
            recent_observations=sorted(observations, key=lambda x: x.timestamp, reverse=True),
        )
