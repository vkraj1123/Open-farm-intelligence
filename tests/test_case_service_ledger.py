from datetime import datetime, timezone

from ofi.domain.models import Farm, FarmCase, Parcel, CropCycle, GeoPoint
from ofi.services.case_manager import CaseManager
from ofi.services.service_directory import ServiceCapability, ServiceDirectory, ServiceProvider
from ofi.services.service_orchestrator import ServiceOrchestrator
from ofi.services.action_router import ActionRequest, ActionRouter
from ofi.services.execution_gateway import ActorIdentity, ConsentGrant, MockServiceAdapter, ServiceExecutionGateway


def case():
    return FarmCase(
        id="case-ledger",
        farm=Farm(
            id="farm-ledger",
            farmer_id="farmer-1",
            parcel=Parcel(
                id="parcel-1",
                location=GeoPoint(latitude=25.0, longitude=72.0),
            ),
            crop_cycle=CropCycle(id="crop-1", crop="bajra"),
        ),
        query="soil test",
    )


def test_case_ledger_records_provider_selection_and_execution():
    manager = CaseManager()
    manager.create(case())

    action = ActionRequest(
        id="case-ledger-action-1",
        case_id="case-ledger",
        farm_id="farm-ledger",
        action="REQUEST_TEST",
        service="soil_test",
        confidence=0.8,
        rationale="Verify soil condition.",
    )
    directory = ServiceDirectory([
        ServiceProvider(
            provider_id="lab-01",
            name="Soil Lab",
            capabilities=(
                ServiceCapability(
                    service="soil_test",
                    capability="soil_testing",
                    action_types=frozenset({"REQUEST_TEST"}),
                ),
            ),
        )
    ])
    selection = ServiceOrchestrator(directory).select(action)
    manager.record_provider_selection("case-ledger", selection)

    gateway = ServiceExecutionGateway([
        MockServiceAdapter("soil_test", provider_id="lab-01")
    ])
    receipt = gateway.submit(
        action=action,
        route=ActionRouter().route_action(action),
        actor=ActorIdentity("farmer-1", "farmer"),
        consent=ConsentGrant(
            "farmer-1", "soil_test", "granted", datetime.now(timezone.utc)
        ),
        provider_id=selection.provider_id,
    )
    manager.record_execution("case-ledger", receipt, "farmer-1")

    record = manager.store.get("case-ledger")
    assert [event.event_type for event in record.case.events] == [
        "reported",
        "provider_selected",
        "service_execution",
    ]
    assert record.case.events[-1].payload["provider_id"] == "lab-01"
    assert record.case.events[-1].payload["transaction_id"] == receipt.transaction_id


def test_case_ledger_records_transaction_status():
    manager = CaseManager()
    manager.create(case())

    action = ActionRequest(
        id="case-ledger-action-2",
        case_id="case-ledger",
        farm_id="farm-ledger",
        action="REQUEST_TEST",
        service="soil_test",
        confidence=0.8,
        rationale="Verify soil condition.",
    )
    gateway = ServiceExecutionGateway([
        MockServiceAdapter("soil_test", provider_id="lab-01")
    ])
    receipt = gateway.submit(
        action=action,
        route=ActionRouter().route_action(action),
        actor=ActorIdentity("farmer-1", "farmer"),
        consent=ConsentGrant(
            "farmer-1", "soil_test", "granted", datetime.now(timezone.utc)
        ),
        provider_id="lab-01",
    )
    transaction = gateway.update_status(
        receipt.transaction_id,
        "accepted",
        external_reference="LAB-123",
    )
    manager.record_transaction_status("case-ledger", transaction)

    record = manager.store.get("case-ledger")
    event = record.case.events[-1]
    assert event.event_type == "service_transaction_status"
    assert event.payload["status"] == "accepted"
    assert event.payload["external_reference"] == "LAB-123"
