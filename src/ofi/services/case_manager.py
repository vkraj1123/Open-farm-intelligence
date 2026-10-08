from ofi.domain.models import CaseEvent, CaseOutcome, CaseRecord, FarmCase, Observation
from ofi.intelligence.orchestrator import Orchestrator
from ofi.geospatial.analytics import derived_ndvi_observation
from ofi.science.registry import default_scientific_registry
from ofi.twin.farm_twin import FarmTwinStore
from ofi.services.case_repository import CaseRepository, InMemoryCaseRepository
from ofi.services.unit_of_work import InMemoryFarmCaseUnitOfWork, UnitOfWork


class InMemoryCaseStore(InMemoryCaseRepository):
    """Backward-compatible name for the reference in-memory repository."""

    pass


class CaseManager:
    def __init__(
        self,
        store=None,
        orchestrator=None,
        farm_twin=None,
        scientific_models=None,
        unit_of_work: UnitOfWork | None = None,
    ):
        self.orchestrator = orchestrator or Orchestrator()
        self.scientific_models = scientific_models or default_scientific_registry()
        if unit_of_work is not None:
            if store is not None or farm_twin is not None:
                raise ValueError("provide repositories through unit_of_work, not alongside it")
            self.unit_of_work = unit_of_work
            self.store = unit_of_work.case_repository
            self.farm_twin = unit_of_work.farm_twin
        else:
            self.store: CaseRepository = store or InMemoryCaseRepository()
            self.farm_twin = farm_twin or FarmTwinStore()
            self.unit_of_work = (
                InMemoryFarmCaseUnitOfWork(self.store, self.farm_twin)
                if isinstance(self.store, InMemoryCaseRepository)
                and isinstance(self.farm_twin, FarmTwinStore)
                else UnitOfWork()
            )

    def create(self, case: FarmCase) -> CaseRecord:
        with self.unit_of_work.atomic():
            record = self.store.create(case)
            self.farm_twin.upsert(case.farm)
            for observation in case.observations:
                self.farm_twin.add_observation(case.farm.id, observation)
            return self._event(record, "reported", "system", {"query": case.query})

    def add_observation(self, case_id: str, observation: Observation, actor: str = "system") -> CaseRecord:
        with self.unit_of_work.atomic():
            record = self.store.get(case_id)
            record.case.observations.append(observation)
            self.farm_twin.add_observation(record.case.farm.id, observation)
            if record.case.status == "reported":
                record.case.status = "triaged"
            return self._event(record, "observation_added", actor, {"observation_id": observation.id})

    def reason(self, case_id: str) -> CaseRecord:
        record = self.store.get(case_id)
        reasoning = self.orchestrator.reason(record.case)
        record.latest_reasoning = reasoning
        record.case.status = "actioned"
        return self._event(record, "reasoned", "orchestrator", {"action": reasoning.decision.action})

    def collect_evidence(self, case_id: str, provider_registry, actor: str = "provider_registry") -> CaseRecord:
        with self.unit_of_work.atomic():
            record = self.store.get(case_id)
            snapshot = self.farm_twin.snapshot(record.case.farm.id)
            observations = provider_registry.collect(snapshot)
            if observations and record.case.status == "reported":
                record.case.status = "triaged"
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
            scientific_observations = self.scientific_models.run(self.farm_twin.snapshot(record.case.farm.id))
            for scientific in scientific_observations:
                record.case.observations.append(scientific)
                self.farm_twin.add_observation(record.case.farm.id, scientific)
                self._event(record, "observation_derived", scientific.source, {"observation_id": scientific.id})
            return self.store.get(record.case.id)

    def plan_actions(self, case_id: str, action_planner=None):
        """Create auditable action requests from the latest reasoning."""
        from ofi.services.action_planning import ActionPlanningService
        record = self.store.get(case_id)
        if record.latest_reasoning is None:
            raise ValueError(f"case has no reasoning result: {case_id}")
        planner = action_planner or ActionPlanningService()
        plan = planner.plan(case_id=record.case.id, farm_id=record.case.farm.id, decision=record.latest_reasoning.decision)
        for request in plan.requests:
            self._event(record, "action_planned", "action_planner", {"action_id": request.id, "service": request.service, "action": request.action, "urgency": request.urgency})
        return self.store.get(record.case.id), plan

    def record_provider_selection(self, case_id: str, selection) -> CaseRecord:
        record = self.store.get(case_id)
        return self._event(record, "provider_selected", "service_orchestrator", {
            "request_id": selection.request.request_id,
            "action_id": selection.request.action_id,
            "service": selection.request.service,
            "capability": selection.request.capability,
            "provider_id": selection.provider_id,
            "rationale": selection.rationale,
            "constraints": selection.request.constraints,
        })

    def record_execution(self, case_id: str, receipt, actor_id: str) -> CaseRecord:
        record = self.store.get(case_id)
        return self._event(record, "service_execution", actor_id, {
            "action_id": receipt.action_id,
            "service": receipt.service,
            "provider_id": receipt.provider_id,
            "status": receipt.status,
            "transaction_id": receipt.transaction_id,
            "idempotency_key": receipt.idempotency_key,
            "external_reference": receipt.external_reference,
            "message": receipt.message,
        })

    def record_transaction_status(self, case_id: str, transaction, actor: str = "service_provider") -> CaseRecord:
        record = self.store.get(case_id)
        latest = transaction.events[-1] if transaction.events else None
        if latest is None:
            raise ValueError("transaction has no events")
        return self._event(record, "service_transaction_status", actor, {
            "transaction_id": transaction.transaction_id,
            "action_id": transaction.action_id,
            "provider_id": transaction.provider_id,
            "status": transaction.status,
            "external_reference": transaction.external_reference,
            "message": latest.message,
        })

    def record_outcome(self, case_id: str, outcome: CaseOutcome, actor: str = "farmer") -> CaseRecord:
        with self.unit_of_work.atomic():
            record = self.store.get(case_id)
            record.outcome = outcome
            record.case.status = "resolved" if outcome.outcome == "resolved" else "observing"
            for observation in outcome.evidence:
                record.case.observations.append(observation)
                self.farm_twin.add_observation(record.case.farm.id, observation)
            return self._event(record, "outcome_recorded", actor, {"outcome": outcome.outcome})

    def record_action_outcome(self, case_id: str, action, outcome, feedback_service=None):
        """Attach an observed action outcome and generate a bounded learning signal."""
        from ofi.services.outcome_feedback import OutcomeFeedbackService
        record = self.store.get(case_id)
        service = feedback_service or OutcomeFeedbackService()
        signal = service.evaluate(action, outcome)
        record = self._event(record, "action_outcome_recorded", "outcome_feedback", {
            "action_id": action.id,
            "service": action.service,
            "effectiveness": outcome.effectiveness,
            "attribution_confidence": outcome.attribution_confidence,
            "learning_signal": signal.signal,
            "learning_weight": signal.weight,
        })
        return record, signal

    def escalate(self, case_id: str, actor: str = "system") -> CaseRecord:
        record = self.store.get(case_id)
        record.case.status = "escalated"
        return self._event(record, "escalated", actor, {"services": ["KVK", "agriculture_extension"]})

    def _event(self, record, event_type, actor, payload) -> CaseRecord:
        event = CaseEvent(event_type=event_type, actor=actor, payload=payload)
        return self.store.save_and_append_event(record, event)
