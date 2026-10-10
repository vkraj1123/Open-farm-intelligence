from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Literal

from ofi.services.action_router import ActionStatus


AttemptStatus = Literal[
    "submitted", "accepted", "in_progress", "completed", "rejected", "failed", "unknown",
]

TransactionStatus = Literal[
    "planned", "submitted", "accepted", "in_progress",
    "completed", "rejected", "failed", "cancelled",
]

_ALLOWED_TRANSITIONS = {
    "planned": {"submitted", "cancelled"},
    "submitted": {"accepted", "rejected", "failed", "cancelled", "in_progress"},
    "accepted": {"in_progress", "completed", "failed", "cancelled"},
    "in_progress": {"completed", "failed", "cancelled"},
    "completed": set(),
    "rejected": set(),
    "failed": set(),
    "cancelled": set(),
}


@dataclass
class ExecutionAttempt:
    attempt_id: str
    transaction_id: str
    attempt_number: int
    provider_id: str
    status: AttemptStatus
    created_at: datetime
    updated_at: datetime
    external_reference: str | None = None
    last_error: str | None = None
    retry_request_key: str | None = None
    retry_of_attempt_id: str | None = None


@dataclass(frozen=True)
class TransactionEvent:
    transaction_id: str
    status: TransactionStatus
    occurred_at: datetime
    external_reference: str | None = None
    message: str = ""


TRANSACTION_TO_ACTION_STATUS = {
    "planned": "planned",
    "submitted": "routed",
    "accepted": "accepted",
    "in_progress": "in_progress",
    "completed": "completed",
    "rejected": "failed",
    "failed": "failed",
    "cancelled": "cancelled",
}


def action_status_for_transaction(status: TransactionStatus) -> ActionStatus:
    """Project transaction truth into the action lifecycle vocabulary."""
    return TRANSACTION_TO_ACTION_STATUS[status]


@dataclass
class ServiceTransaction:
    transaction_id: str
    idempotency_key: str
    action_id: str
    provider_id: str | None
    request_fingerprint: str = ""
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    status: TransactionStatus = "planned"
    external_reference: str | None = None
    events: list[TransactionEvent] = field(default_factory=list)

    @property
    def action_status(self) -> ActionStatus:
        return action_status_for_transaction(self.status)

    def transition(
        self,
        status: TransactionStatus,
        *,
        external_reference: str | None = None,
        message: str = "",
    ) -> TransactionEvent:
        if status not in _ALLOWED_TRANSITIONS[self.status]:
            raise ValueError(f"invalid transition: {self.status} -> {status}")
        now = datetime.now(timezone.utc)
        if external_reference is not None:
            self.external_reference = external_reference
        self.status = status
        event = TransactionEvent(
            transaction_id=self.transaction_id,
            status=status,
            occurred_at=now,
            external_reference=self.external_reference,
            message=message,
        )
        self.events.append(event)
        return event
