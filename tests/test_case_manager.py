from datetime import datetime, timezone
from ofi.domain.models import CropCycle, Farm, FarmCase, GeoPoint, Parcel, Observation, CaseOutcome
from ofi.services.case_manager import CaseManager

def case():
    return FarmCase(id="c1", farm=Farm(id="f1", farmer_id="u1", parcel=Parcel(id="p1",location=GeoPoint(latitude=27,longitude=72)), crop_cycle=CropCycle(crop="bajra")), query="yellowing")

def test_case_lifecycle():
    m=CaseManager(); r=m.create(case()); assert r.case.status=="reported"
    r=m.add_observation("c1", Observation(id="o1",kind="farmer_report",timestamp=datetime.now(timezone.utc),value={"symptom":"yellowing"},source="farmer_report"))
    assert r.case.status=="triaged" and len(r.case.events)==2
    r=m.record_outcome("c1",CaseOutcome(outcome="improved",notes="better"))
    assert r.case.status=="observing" and r.outcome.outcome=="improved"

def test_duplicate_case_rejected():
    m=CaseManager(); m.create(case())
    try: m.create(case())
    except ValueError: return
    assert False
