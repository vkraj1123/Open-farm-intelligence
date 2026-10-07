from datetime import datetime, timezone

import pytest

from ofi.services.action_router import ActionRequest
from ofi.services.outcome_feedback import ActionOutcome, OutcomeFeedbackService


def action():
    return ActionRequest(
        id="case-1-action-1",
        case_id="case-1",
        farm_id="farm-1",
        action="REQUEST_TEST",
        service="soil_test",
        confidence=0.8,
        rationale="Verify soil moisture.",
    )


def test_positive_outcome_creates_weighted_learning_signal():
    signal = OutcomeFeedbackService().evaluate(
        action(),
        ActionOutcome(
            action_id="case-1-action-1",
            service="soil_test",
            case_id="case-1",
            farm_id="farm-1",
            effectiveness="positive",
            observed_at=datetime.now(timezone.utc),
            notes="Field condition improved.",
            attribution_confidence=0.8,
        ),
    )
    assert signal.signal == 1
    assert signal.weight == 0.8


def test_mismatched_action_cannot_create_feedback():
    with pytest.raises(ValueError):
        OutcomeFeedbackService().evaluate(
            action(),
            ActionOutcome(
                action_id="other",
                service="soil_test",
                case_id="case-1",
                farm_id="farm-1",
                effectiveness="positive",
                observed_at=datetime.now(timezone.utc),
            ),
        )


def test_unknown_outcome_has_no_learning_signal():
    signal = OutcomeFeedbackService().evaluate(
        action(),
        ActionOutcome(
            action_id="case-1-action-1",
            service="soil_test",
            case_id="case-1",
            farm_id="farm-1",
            effectiveness="unknown",
            observed_at=datetime.now(timezone.utc),
            attribution_confidence=1.0,
        ),
    )
    assert signal.signal == 0
