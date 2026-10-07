from datetime import datetime, timezone

from ofi.domain.models import CropCycle, Farm, FarmCase, GeoPoint, Observation, Parcel
from ofi.intelligence.orchestrator import Orchestrator
from ofi.services.reasoning_update import ReasoningUpdateService


def make_case():
    now = datetime.now(timezone.utc)
    farm = Farm(
        id="f1",
        farmer_id="u1",
        parcel=Parcel(
            id="p1",
            location=GeoPoint(latitude=27, longitude=72),
            boundary=[
                GeoPoint(latitude=27, longitude=72),
                GeoPoint(latitude=27, longitude=72.01),
                GeoPoint(latitude=27.01, longitude=72.01),
            ],
        ),
        crop_cycle=CropCycle(id="c1", crop="bajra"),
    )
    return FarmCase(id="case-1", farm=farm, query="crop is stressed", observations=[
        Observation(
            id="soil-1", kind="soil", timestamp=now,
            value={"moisture_pct": 19}, source="soil_lab",
        ),
        Observation(
            id="rain-1", kind="weather", timestamp=now,
            value={"rainfall_mm_next_3d": 0}, source="weather_model",
        ),
    ])


def test_reasoning_update_detects_initial_decision():
    case = make_case()
    update = ReasoningUpdateService().update(case, None)
    assert update.change.changed is True
    assert update.change.previous is None
    assert update.change.current in {"REQUEST_TEST", "ESCALATE_EXPERT", "ADVISE", "ASK_FARMER"}


def test_reasoning_update_does_not_mark_small_confidence_shift_as_change():
    case = make_case()
    service = ReasoningUpdateService()
    previous = Orchestrator().reason(case)
    update = service.update(case, previous)
    assert update.change.changed is False
