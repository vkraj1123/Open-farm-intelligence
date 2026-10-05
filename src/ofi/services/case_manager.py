from datetime import datetime, timezone

from ofi.domain.models import CaseEvent, CaseOutcome, CaseRecord, FarmCase, Observation
from ofi.intelligence.orchestrator import Orchestrator
from ofi.geospatial.analytics import derived_ndvi_observation
from ofi.science.engine import derive_water_balance_from_snapshot
from ofi.twin.farm_twin import FarmTwinStore


class InMemoryCaseStore:
    """MVP store; replaceable by Postgres/PostGIS later."""

    def __init__(self):
        self._records = {}

    def create(self, case):
        if case.id in self._records:
            raise ValueError(f"case already exists: {case.id}")
        record = CaseRecord(case=case)
        self._records[case.id] = record
        return record

    def get(self, case_id):
        try:
            return self._records[case_id]
        except KeyError as exc:
            raise KeyError(case_id) from exc

    def save(self, record):
        record.case.updated_at = datetime.now(timezone.utc)
        self._records[record.case.id] = record
        return record


class CaseManager:
    def __init__(self, store=None, orchestrator=None, farm_twin=None):
        self.store = store or InMemoryCaseStore()
        self.orchestrator = orchestrator or Orchestrator()
        self.farm_twin = farm_twin or FarmTwinStore()

    def create(self, case: FarmCase) -> CaseRecord:
        record = self.store.create(case)
        self.farm_twin.upsert(case.farm)
        for observation in case.observations:
            self.farm_twin.add_observation(case.farm.id, observation)
        self._event(record, "reported", "system", {"query": case.query})
        return self.store.save(record)

    def add_observation(self, case_id: str, observation: Observation, actor: str = "system") -> CaseRecord:
        record = self.store.get(case_id)
        record.case.observations.append(observation)
        self.farm_twin.add_observation(record.case.farm.id, observation)
        self._event(record, "observation_added", actor, {"observation_id": observation.id})
        if record.case.status == "reported":
            record.case.status = "triaged"
        return self.store.save(record)

    def reason(self, case_id: str) -> CaseRecord:
        record = self.store.get(case_id)
        reasoning = self.orchestrator.reason(record.case)
        record.latest_reasoning = reasoning
        record.case.status = "actioned"
        self._event(record, "reasoned", "orchestrator", {"action": reasoning.decision.action})
        return self.store.save(record)

    def collect_evidence(self, case_id: str, provider_registry, actor: str = "provider_registry") -> CaseRecord:
        record = self.store.get(case_id)
        snapshot = self.farm_twin.snapshot(record.case.farm.id)
        observations = provider_registry.collect(snapshot)
        for observation in observations:
            record.case.observations.append(observation)
            self.farm_twin.add_observation(record.case.farm.id, observation)
            self._event(record, "observation_added", actor, {"observation_id": observation.id, "source": observation.source})
        snapshot = self.farm_twin.snapshot(record.case.farm.id)
        derived = derived_ndvi_observation(snapshot)
        if derived:
            record.case.observations.append(derived)
            self.farm_twin.add_observation(record.case.farm.id, derived)
            self._event(record, "observation_derived", "geospatial_analytics", {"observation_id": derived.id})
        scientific = derive_water_balance_from_snapshot(self.farm_twin.snapshot(record.case.farm.id))
        if scientific:
            record.case.observations.append(scientific)
            self.farm_twin.add_observation(record.case.farm.id, scientific)
            self._event(record, "observation_derived", "scientific_engine", {"observation_id": scientific.id})
        if observations and record.case.status == "reported":
            record.case.status = "triaged"
        return self.store.save(record)

    def record_outcome(self, case_id: str, outcome: CaseOutcome, actor: str = "farmer") -> CaseRecord:
        record = self.store.get(case_id)
        record.outcome = outcome
        record.case.status = "resolved" if outcome.outcome == "resolved" else "observing"
        for observation in outcome.evidence:
            record.case.observations.append(observation)
            self.farm_twin.add_observation(record.case.farm.id, observation)
        self._event(record, "outcome_recorded", actor, {"outcome": outcome.outcome})
        return self.store.save(record)

    def escalate(self, case_id: str, actor: str = "system") -> CaseRecord:
        record = self.store.get(case_id)
        record.case.status = "escalated"
        self._event(record, "escalated", actor, {"services": ["KVK", "agriculture_extension"]})
        return self.store.save(record)

    @staticmethod
    def _event(record, event_type, actor, payload):
        record.case.events.append(
            CaseEvent(
                id=f"evt-{len(record.case.events) + 1}",
                event_type=event_type,
                actor=actor,
                payload=payload,
            )
        )
