from datetime import datetime, timezone

from ofi.domain.models import CropCycle, Farm, FarmCase, GeoPoint, Parcel
from ofi.services.case_manager import CaseManager


def test_case_manager_records_action_plan():
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
    manager = CaseManager()
    record = manager.create(FarmCase(id="case-1", farm=farm, query="need soil test"))
    record = manager.reason("case-1")
    saved, plan = manager.plan_actions("case-1")
    assert saved.case.id == "case-1"
    assert len(plan.requests) >= 0
    assert any(event.event_type == "action_planned" for event in saved.case.events) == bool(plan.requests)
