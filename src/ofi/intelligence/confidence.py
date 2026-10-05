from collections import defaultdict

from ofi.domain.models import Evidence, Hypothesis


def rank_hypotheses(evidence: list[Evidence], observations: dict[str, dict]) -> list[Hypothesis]:
    """Small deterministic MVP rule engine.

    These scores are engineering heuristics, not calibrated agronomic probabilities.
    They establish a testable substrate before scientific models are connected.
    """
    by_obs = {item_id: observations[item_id] for item_id in observations}
    scores = defaultdict(float)
    support: dict[str, list[str]] = defaultdict(list)
    contradict: dict[str, list[str]] = defaultdict(list)

    def obs(kind: str, key: str, expected=None):
        for item in by_obs.values():
            if item.get("kind") == kind and key in item.get("value", {}):
                value = item["value"][key]
                if expected is None or expected(value):
                    return item
        return None

    moisture = obs("soil", "moisture_pct", lambda x: x < 25)
    rain = obs("weather", "rainfall_mm_next_3d", lambda x: x < 5)
    ndvi = obs("satellite", "ndvi_trend", lambda x: x < -0.05)
    disease = obs("image", "disease_signs", lambda x: bool(x))
    adequate_moisture = obs("soil", "moisture_pct", lambda x: x >= 25)

    for code, matches in {
        "water_stress": [moisture, rain, ndvi],
        "disease_stress": [disease, adequate_moisture, ndvi],
    }.items():
        for match in matches:
            if match:
                ev = next((e for e in evidence if match["id"] in e.observation_ids), None)
                if ev:
                    scores[code] += ev.score
                    support[code].append(ev.id)

    labels = {
        "water_stress": "Possible water stress",
        "disease_stress": "Possible disease-related stress",
    }
    results = []
    for code, score in scores.items():
        normalized = min(1.0, score / 2.4)
        results.append(
            Hypothesis(
                code=code,
                label=labels[code],
                score=round(normalized, 3),
                supporting_evidence=support[code],
                contradicting_evidence=contradict[code],
            )
        )
    return sorted(results, key=lambda x: x.score, reverse=True)
