from datetime import datetime, timezone
from ofi.domain.models import Farm, FarmCase, Observation, Parcel, CropCycle, GeoPoint
from ofi.services.case_manager import CaseManager
from ofi.services.action_planning import ActionPlanningService
from ofi.services.service_directory import ServiceDirectory, ServiceProvider, ServiceCapability
from ofi.services.service_orchestrator import ServiceOrchestrator
from ofi.services.execution_gateway import ServiceExecutionGateway, MockServiceAdapter, ActorIdentity, ConsentGrant
from ofi.services.action_execution import ActionExecutionService
from ofi.services.outcome_feedback import ActionOutcome

def test_closed_loop():
    now = datetime.now(timezone.utc)
    case = FarmCase(
        id="closed-loop",
        farm=Farm(
            id="farm-closed",
            farmer_id="farmer-1",
            parcel=Parcel(id="parcel-1", location=GeoPoint(latitude=25, longitude=72)),
            crop_cycle=CropCycle(id="crop-1", crop="bajra"),
        ),
        query="field is dry",
        observations=[
            Observation(id="soil-1", kind="soil", timestamp=now, value={"moisture_pct": 18}, source="soil_lab"),
            Observation(id="weather-1", kind="weather", timestamp=now, value={"rainfall_mm_next_3d": 1, "rainfall_mm_last_7d": 4}, source="weather_model"),
        ],
    )
    manager = CaseManager()
    manager.create(case)
    manager.reason(case.id)
    _, plan = manager.plan_actions(case.id)
    action = plan.requests[0]
    directory = ServiceDirectory([ServiceProvider(
        provider_id="lab-01",
        name="Soil Lab",
        capabilities=(ServiceCapability(
            service="soil_test", capability="soil_testing",
            action_types=frozenset({"REQUEST_TEST"}), regions=frozenset({"Balotra"})
        ),)
    )])
    selection = ServiceOrchestrator(directory).select(action, region="Balotra")
    manager.record_provider_selection(case.id, selection)
    gateway = ServiceExecutionGateway([MockServiceAdapter("soil_test", provider_id="lab-01")])
    receipt, _ = ActionExecutionService(gateway).execute(
        action=action,
        actor=ActorIdentity("farmer-1", "farmer"),
        consent=ConsentGrant("farmer-1", "soil_test", "granted", now),
        provider_id=selection.provider_id,
    )
    manager.record_execution(case.id, receipt, "farmer-1")
    transaction = gateway.update_status(receipt.transaction_id, "accepted", external_reference="LAB-123")
    manager.record_transaction_status(case.id, transaction)
    transaction = gateway.update_status(receipt.transaction_id, "in_progress", external_reference="LAB-123")
    manager.record_transaction_status(case.id, transaction)
    transaction = gateway.update_status(receipt.transaction_id, "completed", external_reference="LAB-123")
    manager.record_transaction_status(case.id, transaction)
    outcome = ActionOutcome(
        action_id=action.id, service=action.service, case_id=case.id, farm_id=case.farm.id,
        effectiveness="positive", observed_at=now, attribution_confidence=0.8
    )
    _, signal = manager.record_action_outcome(case.id, action, outcome)
    assert signal.signal == 1
    assert signal.weight == 0.8
    events = manager.store.get(case.id).case.events
    assert [e.event_type for e in events] == [
        "reported", "reasoned", "action_planned", "provider_selected",
        "service_execution", "service_transaction_status",
        "service_transaction_status", "service_transaction_status",
        "action_outcome_recorded"
    ]
    assert events[4].payload["provider_id"] == "lab-01"
    assert events[4].payload["transaction_id"] == receipt.transaction_id
