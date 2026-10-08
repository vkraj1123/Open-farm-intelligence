from datetime import datetime, timezone

from ofi.domain.models import CropCycle, Farm, FarmCase, GeoPoint, Parcel
from ofi.services.case_manager import CaseManager
from ofi.services.outcome_feedback import ActionOutcome


def make_case():
    farm = Farm(
        id="farm-1",
        farmer_id="farmer-1",
        parcel=Parcel(
            id="parcel-1",
            location=GeoPoint(latitude=27, longitude=72),
            boundary=[
                GeoPoint(latitude=27, longitude=72),
                GeoPoint(latitude=27, longitude=72.01),
                GeoPoint(latitude=27.01, longitude=72.01),
            ],
        ),
        crop_cycle=CropCycle(id="crop-1", crop="bajra"),
    )
    return FarmCase(id="case-1", farm=farm, query="need soil test")


def test_action_outcome_is_audited():
    manager = CaseManager()
    manager.create(make_case())
    record = manager.reason("case-1")
    saved, plan = manager.plan_actions("case-1")
    if not plan.requests:
        return

    action = plan.requests[0]
    outcome = ActionOutcome(
        action_id=action.id,
        service=action.service,
        case_id="case-1",
        farm_id="farm-1",
        effectiveness="positive",
        observed_at=datetime.now(timezone.utc),
        attribution_confidence=0.75,
    )
    saved, signal = manager.record_action_outcome("case-1", action, outcome)
    assert signal.signal == 1
    assert any(e.event_type == "action_outcome_recorded" for e in saved.case.events)
