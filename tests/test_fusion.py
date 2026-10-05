from datetime import datetime, timedelta, timezone

from ofi.domain.models import CropCycle, Farm, FarmCase, GeoPoint, Observation, Parcel
from ofi.intelligence.fusion import make_evidence


def test_fresh_high_quality_source_scores_higher():
    now = datetime.now(timezone.utc)
    base = Farm(id="f", farmer_id="u", parcel=Parcel(id="p", location=GeoPoint(latitude=27, longitude=72)), crop_cycle=CropCycle(crop="bajra"))
    case = FarmCase(id="c", farm=base, query="test", observations=[
        Observation(id="a", kind="soil", timestamp=now, value={}, source="soil_lab"),
        Observation(id="b", kind="soil", timestamp=now - timedelta(days=30), value={}, source="farmer_report"),
    ])
    scores = make_evidence(case)
    assert scores[0].score > scores[1].score
