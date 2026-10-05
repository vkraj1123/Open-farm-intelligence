from collections import defaultdict

from ofi.domain.models import Evidence, Hypothesis


RULES = {
    "water_stress": {
        "label": "Possible water stress",
        "support": [
            ("soil", "moisture_pct", lambda x: x < 25, 1.0),
            ("weather", "rainfall_mm_next_3d", lambda x: x < 5, 0.8),
            ("satellite", "ndvi_trend", lambda x: x < -0.05, 0.8),
            ("satellite", "ndwi", lambda x: x < -0.10, 0.6),
        ],
        "contradict": [
            ("soil", "moisture_pct", lambda x: x >= 35, 0.8),
            ("weather", "rainfall_mm_last_7d", lambda x: x >= 30, 0.6),
        ],
    },
    "disease_stress": {
        "label": "Possible disease-related stress",
        "support": [
            ("image", "disease_signs", lambda x: bool(x), 1.0),
            ("soil", "moisture_pct", lambda x: x >= 25, 0.4),
            ("satellite", "ndvi_trend", lambda x: x < -0.05, 0.7),
        ],
        "contradict": [
            ("image", "disease_signs", lambda x: not bool(x), 0.7),
        ],
    },
}


def rank_hypotheses(evidence: list[Evidence], observations: dict[str, dict]) -> list[Hypothesis]:
    """Transparent MVP agronomic rule engine.

    Thresholds are explicit engineering hypotheses, not calibrated probabilities.
    Scientific crop/region models can replace these rules behind the same interface.
    """
    by_kind: dict[str, list[dict]] = defaultdict(list)
    for item in observations.values():
        by_kind[item["kind"]].append(item)

    evidence_by_observation = {
        obs_id: item for item in evidence for obs_id in item.observation_ids
    }
    results: list[Hypothesis] = []

    for code, rule in RULES.items():
        support_ids: list[str] = []
        contradict_ids: list[str] = []
        support_score = 0.0
        support_weight = 0.0
        contradict_score = 0.0

        for kind, key, predicate, weight in rule["support"]:
            for item in by_kind.get(kind, []):
                if key in item.get("value", {}) and predicate(item["value"][key]):
                    ev = evidence_by_observation.get(item["id"])
                    if ev:
                        support_ids.append(ev.id)
                        support_score += ev.score * weight
                        support_weight += weight
                        break

        for kind, key, predicate, weight in rule["contradict"]:
            for item in by_kind.get(kind, []):
                if key in item.get("value", {}) and predicate(item["value"][key]):
                    ev = evidence_by_observation.get(item["id"])
                    if ev:
                        contradict_ids.append(ev.id)
                        contradict_score += ev.score * weight
                        break

        if support_weight == 0:
            continue

        support = support_score / support_weight
        penalty = min(0.8, contradict_score / 2.0)
        score = max(0.0, min(1.0, support * (1.0 - penalty)))

        results.append(Hypothesis(
            code=code,
            label=rule["label"],
            score=round(score, 3),
            supporting_evidence=support_ids,
            contradicting_evidence=contradict_ids,
        ))

    return sorted(results, key=lambda item: item.score, reverse=True)
