from datetime import datetime, timezone

from ofi.domain.models import Evidence, FarmCase


SOURCE_RELIABILITY = {
    "expert": 1.00,
    "soil_lab": 0.95,
    "calibrated_sensor": 0.90,
    "satellite": 0.85,
    "sentinel": 0.85,
    "weather_station": 0.90,
    "weather_model": 0.75,
    "soil_sensor": 0.85,
    "ofi_geospatial_analytics": 0.80,
    "farmer_photo": 0.75,
    "farmer_report": 0.70,
    "model": 0.60,
    "ofi_fao56_water_balance": 0.80,
}


def freshness(timestamp: datetime, kind: str) -> float:
    age_days = max(0.0, (datetime.now(timezone.utc) - timestamp.astimezone(timezone.utc)).total_seconds() / 86400)
    window = {
        "weather": 3.0, "sensor": 7.0, "soil": 60.0, "satellite": 14.0,
        "image": 7.0, "farmer_report": 7.0, "market": 2.0,\n        "model": 7.0,
    }.get(kind, 14.0)
    return max(0.0, 1.0 - age_days / window)


def make_evidence(case: FarmCase) -> list[Evidence]:
    result = []
    for obs in case.observations:
        fresh = freshness(obs.timestamp, obs.kind)
        result.append(Evidence(
            id=f"ev-{obs.id}",
            proposition=f"observation:{obs.id}",
            observation_ids=[obs.id],
            score=round(SOURCE_RELIABILITY.get(obs.source, 0.60) * obs.quality * obs.confidence * fresh, 3),
            freshness=round(fresh, 3),
            quality=obs.quality,
        ))
    return result
