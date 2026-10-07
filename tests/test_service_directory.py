from ofi.services.service_directory import (
    ServiceCapability,
    ServiceDirectory,
    ServiceProvider,
)


def directory():
    return ServiceDirectory([
        ServiceProvider(
            provider_id="kvk-balotra",
            name="KVK Balotra",
            capabilities=(
                ServiceCapability(
                    service="KVK",
                    capability="agriculture_extension",
                    action_types=frozenset({"ESCALATE_EXPERT"}),
                    regions=frozenset({"Balotra"}),
                    languages=frozenset({"hi"}),
                ),
            ),
        ),
        ServiceProvider(
            provider_id="lab-01",
            name="Soil Lab",
            capabilities=(
                ServiceCapability(
                    service="soil_test",
                    capability="soil_testing",
                    action_types=frozenset({"REQUEST_TEST"}),
                ),
            ),
        ),
    ])


def test_discover_matches_capability_and_constraints():
    matches = directory().discover(
        service="KVK",
        capability="agriculture_extension",
        action="ESCALATE_EXPERT",
        region="Balotra",
        language="hi",
    )
    assert [m.provider_id for m in matches] == ["kvk-balotra"]
    assert "capability" in matches[0].reasons
    assert "geography" in matches[0].reasons
    assert "language" in matches[0].reasons


def test_discover_does_not_match_wrong_region():
    matches = directory().discover(
        service="KVK",
        capability="agriculture_extension",
        action="ESCALATE_EXPERT",
        region="Jaipur",
        language="hi",
    )
    assert matches == []
