import pytest

from ofi.services.retry_policy import assess_retry


@pytest.mark.parametrize(
    ("attempt_status", "expected"),
    [
        ("unknown", "reconcile_required"),
        ("failed", "reconcile_required"),
        ("rejected", "reconcile_required"),
    ],
)
def test_unreconciled_terminal_or_unknown_attempt_requires_reconciliation(
    attempt_status, expected
):
    assessment = assess_retry(attempt_status=attempt_status)

    assert assessment.decision == expected


@pytest.mark.parametrize("attempt_status", ["submitted", "accepted", "in_progress"])
def test_active_attempt_is_not_retried_without_reconciliation(attempt_status):
    assessment = assess_retry(attempt_status=attempt_status)

    assert assessment.decision == "do_not_retry"


def test_reconciled_non_execution_allows_new_attempt():
    assessment = assess_retry(
        attempt_status="unknown",
        reconciled=True,
        execution_confirmed=False,
    )

    assert assessment.decision == "retry_allowed"


def test_reconciled_execution_blocks_retry():
    assessment = assess_retry(
        attempt_status="unknown",
        reconciled=True,
        execution_confirmed=True,
    )

    assert assessment.decision == "do_not_retry"


def test_local_failure_does_not_override_provider_execution_confirmation():
    assessment = assess_retry(
        attempt_status="failed",
        reconciled=True,
        execution_confirmed=True,
    )

    assert assessment.decision == "do_not_retry"
