from dataclasses import dataclass
from typing import Literal

RetryDecision = Literal["retry_allowed", "reconcile_required", "do_not_retry"]

@dataclass(frozen=True)
class RetryAssessment:
    decision: RetryDecision
    reason: str


def assess_retry(*, attempt_status: str, reconciled: bool = False, execution_confirmed: bool | None = None) -> RetryAssessment:
    """Assess whether creating another external execution attempt is safe."""
    if execution_confirmed is True:
        return RetryAssessment("do_not_retry", "provider reconciliation confirms external execution")
    if reconciled and execution_confirmed is False:
        return RetryAssessment("retry_allowed", "provider reconciliation confirms the attempt was not executed")
    if attempt_status in {"completed", "accepted", "in_progress", "submitted"}:
        return RetryAssessment("do_not_retry", "attempt may still represent an active external execution")
    return RetryAssessment("reconcile_required", "external execution state is not proven safe for retry")
