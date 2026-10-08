from datetime import datetime, timezone

from ofi.domain.models import CropCycle, Farm, FarmCase, GeoPoint, Parcel
from ofi.services.action_router import ActionRequest
from ofi.services.experience_memory import ExperienceMemory
from ofi.services.experience_summary import summarize_experience
from ofi.services.outcome_feedback import ActionOutcome


def case(crop, district):
    return FarmCase(
        id=f"case-{crop}-{district}",
        farm=Farm(
            id=f"farm-{crop}-{district}",
            farmer_id="u",
            parcel=Parcel(
                id="p",
                location=GeoPoint(latitude=27, longitude=72),
                administrative_area={"district": district},
            ),
            crop_cycle=CropCycle(id="c", crop=crop, season="kharif"),
        ),
        query="stress",
    )


def action(case_id, service):
    return ActionRequest(
        id=f"{case_id}-action-1",
        case_id=case_id,
        farm_id="farm",
        action="REQUEST_TEST",
        service=service,
        confidence=0.8,
        rationale="test",
    )


def outcome(action_id, service, effect):
    return ActionOutcome(
        action_id=action_id,
        service=service,
        case_id="case",
        farm_id="farm",
        effectiveness=effect,
        observed_at=datetime.now(timezone.utc),
        attribution_confidence=1.0,
    )


def test_summary_preserves_small_sample_caveat():
    memory = ExperienceMemory()
    c = case("bajra", "Balotra")
    a = action(c.id, "soil_test")
    memory.add(action=a, outcome=outcome(a.id, a.service, "positive"), case=c)
    summary = summarize_experience(memory.retrieve(c, service="soil_test"))
    assert summary[0].sample_size == 1
    assert summary[0].confidence < 1
    assert "causal" in summary[0].caveat
