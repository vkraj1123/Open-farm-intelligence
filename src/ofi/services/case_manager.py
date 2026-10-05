from datetime import datetime, timezone

from ofi.domain.models import CaseEvent, CaseOutcome, CaseRecord, FarmCase, Observation
from ofi.twin.farm_twin import FarmTwinStore
from ofi.intelligence.orchestrator import Orchestrator


class InMemoryCaseStore:
    """MVP store; replaceable by Postgres/PostGIS later."""
    def __init__(self): self._records = {}
    def create(self, case):
        if case.id in self._records: raise ValueError(f"case already exists: {case.id}")
        record = CaseRecord(case=case); self._records[case.id] = record; return record
    def get(self, case_id):
        if case_id not in self._records: raise KeyError(case_id)
        return self._records[case_id]
    def save(self, record):
        record.case.updated_at = datetime.now(timezone.utc); self._records[record.case.id] = record; return record


class CaseManager:
    def __init__(self, store=None, orchestrator=None, farm_twin=None):
        self.store = store or InMemoryCaseStore(); self.orchestrator = orchestrator or Orchestrator(); self.farm_twin = farm_twin or FarmTwinStore()
    def create(self, case):
        record=self.store.create(case); self.farm_twin.upsert(case.farm); self._event(record,"reported","system",{"query":case.query}); return self.store.save(record)
    def add_observation(self, case_id, observation, actor="system"):
        record=self.store.get(case_id); record.case.observations.append(observation); self.farm_twin.add_observation(record.case.farm.id, observation)
        self._event(record,"observation_added",actor,{"observation_id":observation.id})
        if record.case.status=="reported": record.case.status="triaged"
        return self.store.save(record)
    def reason(self, case_id):
        record=self.store.get(case_id); reasoning=self.orchestrator.reason(record.case)
        record.latest_reasoning=reasoning; record.case.status="actioned"
        self._event(record,"reasoned","orchestrator",{"action":reasoning.decision.action}); return self.store.save(record)
    def record_outcome(self, case_id, outcome, actor="farmer"):
        record=self.store.get(case_id); record.outcome=outcome
        record.case.status="resolved" if outcome.outcome=="resolved" else "observing"
        record.case.observations.extend(outcome.evidence)
        self._event(record,"outcome_recorded",actor,{"outcome":outcome.outcome}); return self.store.save(record)
    def escalate(self, case_id, actor="system"):
        record=self.store.get(case_id); record.case.status="escalated"
        self._event(record,"escalated",actor,{"services":["KVK","agriculture_extension"]}); return self.store.save(record)
    @staticmethod
    def _event(record, event_type, actor, payload):
        record.case.events.append(CaseEvent(id=f"evt-{len(record.case.events)+1}",event_type=event_type,actor=actor,payload=payload))
