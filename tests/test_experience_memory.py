from datetime import datetime, timezone

from ofi.domain.models import CropCycle, Farm, FarmCase, GeoPoint, Parcel
from ofi.services.action_router import ActionRequest
from ofi.services.experience_memory import ExperienceMemory, context_signature, context_similarity
from ofi.services.outcome_feedback import ActionOutcome


def make_case(crop="bajra", season="kharif", irrigation="drip"):
    farm = Farm(
        id=f"farm-{crop}",
        farmer_id="farmer-1",
        parcel=Parcel(
            id="parcel-1",
            location=GeoPoint(latitude=27, longitude=72),
            boundary=[],
            administrative_area={"district": "Balotra"},
        ),
        crop_cycle=CropCycle(
            id="crop-1",
            crop=crop,
            season=season,
            irrigation_method=irrigation,
        ),
    )
    return FarmCase(id=f"case-{crop}", farm=farm, query="water stress")


def action(case_id="case-1", service="soil_test"):
    return ActionRequest(
        id=f"{case_id}-action-1",
        case_id=case_id,
        farm_id="farm-1",
        action="REQUEST_TEST",
        service=service,
        confidence=0.8,
        rationale="Verify water stress.",
    )


def outcome(action_id, service="soil_test", effectiveness="positive"):
    return ActionOutcome(
        action_id=action_id,
        service=service,
        case_id="case-1",
        farm_id="farm-1",
        effectiveness=effectiveness,
        observed_at=datetime.now(timezone.utc),
        attribution_confidence=0.9,
    )


def test_experience_memory_retrieves_similar_farm_context():
    memory = ExperienceMemory()
    source = make_case()
    memory.add(
        action=action(source.id),
        outcome=outcome(f"{source.id}-action-1"),
        case=source,
        hypothesis_codes=("water_stress",),
    )

    target = make_case()
    matches = memory.retrieve(target, hypothesis_codes=("water_stress",), service="soil_test")

    assert len(matches) == 1
    assert matches[0].similarity > 0.7
    assert matches[0].weighted_signal > 0


def test_experience_memory_does_not_match_different_crop_without_shared_context():
    memory = ExperienceMemory()
    source = make_case(crop="bajra")
    memory.add(
        action=action(source.id),
        outcome=outcome(f"{source.id}-action-1"),
        case=source,
        hypothesis_codes=("water_stress",),
    )
    target = make_case(crop="wheat")
    matches = memory.retrieve(target, hypothesis_codes=("disease_stress",), service="soil_test")
    assert matches == []


def test_similarity_is_explainable_and_bounded():
    a = context_signature(make_case(), ("water_stress",))
    b = context_signature(make_case(), ("water_stress",))
    assert context_similarity(a, b) == 1.0
