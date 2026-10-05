from ofi.domain.models import Decision, FarmCase, ReasoningResult
from ofi.intelligence.confidence import rank_hypotheses
from ofi.intelligence.fusion import make_evidence


class Orchestrator:
    def reason(self, case: FarmCase) -> ReasoningResult:
        evidence = make_evidence(case)
        observations = {
            item.id: {"id": item.id, "kind": item.kind, "value": item.value}
            for item in case.observations
        }
        hypotheses = rank_hypotheses(evidence, observations)

        if not hypotheses:
            decision = Decision(
                action="ASK_FARMER",
                confidence=0.0,
                rationale="There is not enough structured evidence to form a useful hypothesis.",
                next_questions=[
                    "When did the symptoms start?",
                    "What crop growth stage is the field in?",
                    "When was the field last irrigated or exposed to rainfall?",
                ],
            )
        else:
            top = hypotheses[0]
            margin = top.score - (hypotheses[1].score if len(hypotheses) > 1 else 0.0)
            if top.score >= 0.75 and margin >= 0.15:
                decision = Decision(
                    action="ADVISE",
                    confidence=top.score,
                    rationale=f"Evidence currently supports {top.label.lower()}.",
                )
            elif top.score >= 0.55:
                decision = Decision(
                    action="REQUEST_TEST",
                    confidence=top.score,
                    rationale="Evidence is suggestive but not strong enough for a direct field intervention.",
                    services=["soil_test"] if top.code == "water_stress" else ["crop_disease_diagnosis"],
                )
            else:
                decision = Decision(
                    action="ESCALATE_EXPERT",
                    confidence=top.score,
                    rationale="The available evidence is weak or conflicting; expert review is safer.",
                    services=["KVK", "agriculture_extension"],
                )

        return ReasoningResult(
            case_id=case.id,
            evidence=evidence,
            hypotheses=hypotheses,
            decision=decision,
        )
