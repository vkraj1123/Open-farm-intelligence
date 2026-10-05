from datetime import datetime, timezone

from ofi.domain.models import CropCycle, Farm, FarmCase, GeoPoint, Observation, Parcel
from ofi.intelligence.orchestrator import Orchestrator


def make_case(observations):
    farm = Farm(
        id="farm-1",
        farmer_id="farmer-1",
        parcel=Parcel(id="parcel-1", location=GeoPoint(latitude=27.0, longitude=72.0)),
        crop_cycle=CropCycle(id="crop-1", crop="bajra"),
    )
    return FarmCase(id="case-1", farm=farm, query="My bajra field is yellowing.", observations=observations)


def test_water_stress_path():
    now = datetime.now(timezone.utc)
    case = make_case([
        Observation(id="soil-1", kind="soil", timestamp=now, value={"moisture_pct": 15}, source="soil_lab"),
        Observation(id="weather-1", kind="weather", timestamp=now, value={"rainfall_mm_next_3d": 0}, source="weather_model"),
        Observation(id="sat-1", kind="satellite", timestamp=now, value={"ndvi_trend": -0.12}, source="satellite"),
    ])
    result = Orchestrator().reason(case)
    assert result.hypotheses[0].code == "water_stress"
    assert result.decision.action == "ADVISE"


def test_uncertain_case_escalates():
    case = make_case([
        Observation(
            id="report-1", kind="farmer_report", timestamp=datetime.now(timezone.utc),
            value={"symptom": "yellowing"}, source="farmer_report"
        )
    ])
    result = Orchestrator().reason(case)
    assert result.decision.action == "ASK_FARMER"


def test_orchestrator_does_not_advise_on_temporally_misaligned_evidence():
    from datetime import datetime, timedelta, timezone
    from ofi.domain.models import Farm, FarmCase, CropCycle, GeoPoint, Observation, Parcel
    from ofi.intelligence.orchestrator import Orchestrator

    now = datetime.now(timezone.utc)
    farm = Farm(id="f-align", farmer_id="u", parcel=Parcel(id="p", location=GeoPoint(latitude=27, longitude=72)),
                crop_cycle=CropCycle(id="c", crop="bajra"))
    case = FarmCase(id="case-align", farm=farm, query="water stress", observations=[
        Observation(id="w-now", kind="weather", timestamp=now, value={"temperature_c": 35}, source="weather_station"),
        Observation(id="w-old", kind="weather", timestamp=now-timedelta(days=10), value={"temperature_c": 10}, source="weather_model"),
    ])
    result = Orchestrator().reason(case)
    assert result.decision.action == "REQUEST_TEST"
    assert "temporal_mismatch" in result.decision.rationale
