from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Literal


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


@dataclass(frozen=True)
class TransactionEvent:
    transaction_id: str
    status: TransactionStatus
    occurred_at: datetime
    external_reference: str | None = None
    message: str = ""


@dataclass
class ServiceTransaction:
    transaction_id: str
    idempotency_key: str
    action_id: str
    provider_id: str | None
    status: TransactionStatus = "planned"
    external_reference: str | None = None
    events: list[TransactionEvent] = field(default_factory=list)

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
