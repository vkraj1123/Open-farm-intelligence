from datetime import datetime, timedelta, timezone

from ofi.domain.models import Observation
from ofi.intelligence.freshness import freshness_for


def observation(kind, hours_ago):
    return Observation(
        id=f"{kind}-{hours_ago}",
        kind=kind,
        timestamp=datetime.now(timezone.utc) - timedelta(hours=hours_ago),
        value={"value": 1},
        source="test",
    )


def test_missing_evidence_is_due():
    state = freshness_for([], kind="weather")
    assert state.state == "missing"
    assert state.due is True


def test_recent_weather_is_fresh():
    state = freshness_for([observation("weather", 24)], kind="weather")
    assert state.state == "fresh"
    assert state.due is False
    assert state.age_hours == 24


def test_old_weather_is_stale_and_due():
    state = freshness_for([observation("weather", 100)], kind="weather")
    assert state.state == "stale"
    assert state.due is True


def test_future_observation_is_not_used():
    state = freshness_for([observation("weather", -1)], kind="weather")
    assert state.state == "missing"
