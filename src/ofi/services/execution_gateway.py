from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal, Protocol

from ofi.services.action_router import ActionRequest, ActionRoute
from ofi.services.service_transaction import ServiceTransaction


ConsentStatus = Literal["granted", "denied", "expired", "not_required"]
ExecutionStatus = Literal[
    "submitted", "accepted", "in_progress", "rejected", "failed", "completed"
]


@dataclass(frozen=True)
class ActorIdentity:
    actor_id: str
    role: Literal["farmer", "cultivator", "owner", "agent", "service_provider", "system"]
    authenticated: bool = True


@dataclass(frozen=True)
class ConsentGrant:
    actor_id: str
    purpose: str
    status: ConsentStatus
    granted_at: datetime
    expires_at: datetime | None = None

    def active(self, *, as_of: datetime | None = None) -> bool:
        moment = (as_of or datetime.now(timezone.utc)).astimezone(timezone.utc)
        if self.status != "granted":
            return False
        return self.expires_at is None or self.expires_at > moment


@dataclass(frozen=True)
class ExecutionRequest:
    action: ActionRequest
    route: ActionRoute
    actor: ActorIdentity
    consent: ConsentGrant | None
    idempotency_key: str


@dataclass(frozen=True)
class ExecutionReceipt:
    action_id: str
    service: str
    status: ExecutionStatus
    external_reference: str | None
    submitted_at: datetime
    message: str
    transaction_id: str
    idempotency_key: str


class ServiceAdapter(Protocol):
    name: str

    def execute(self, request: ExecutionRequest) -> ExecutionReceipt:
        ...


class ExecutionError(Exception):
    pass


class ServiceExecutionGateway:
    """Safe execution boundary with idempotent, traceable transactions."""

    def __init__(self, adapters: list[ServiceAdapter] | None = None):
        self._adapters = {adapter.name: adapter for adapter in adapters or []}
        self._transactions: dict[str, ServiceTransaction] = {}
        self._idempotency: dict[str, str] = {}

    def register(self, adapter: ServiceAdapter) -> None:
        if adapter.name in self._adapters:
            raise ValueError(f"service adapter already registered: {adapter.name}")
        self._adapters[adapter.name] = adapter

    def submit(
        self,
        *,
        action: ActionRequest,
        route: ActionRoute,
        actor: ActorIdentity,
        consent: ConsentGrant | None = None,
        idempotency_key: str | None = None,
        provider_id: str | None = None,
    ) -> ExecutionReceipt:
        if not actor.authenticated:
            raise ExecutionError("authenticated actor is required")
        if consent is None and route.capability not in {"expert", "verification"}:
            raise ExecutionError("explicit consent is required for this service")
        if consent is not None and not consent.active():
            raise ExecutionError("consent is not active")

        key = idempotency_key or f"{action.id}:{route.service}"
        existing_id = self._idempotency.get(key)
        if existing_id:
            return self._receipt_from_transaction(self._transactions[existing_id], route.service)

        adapter = self._adapters.get(route.service)
        if adapter is None:
            raise ExecutionError(f"no execution adapter registered: {route.service}")

        transaction = ServiceTransaction(
            transaction_id=f"txn:{action.id}:{len(self._transactions) + 1}",
            idempotency_key=key,
            action_id=action.id,
            provider_id=provider_id,
        )
        self._transactions[transaction.transaction_id] = transaction
        self._idempotency[key] = transaction.transaction_id
        transaction.transition("submitted")

        request = ExecutionRequest(
            action=action,
            route=route,
            actor=actor,
            consent=consent,
            idempotency_key=key,
        )
        receipt = adapter.execute(request)
        if receipt.status != "submitted":
            transaction.transition(
                receipt.status,
                external_reference=receipt.external_reference,
                message=receipt.message,
            )
        return ExecutionReceipt(
            action_id=receipt.action_id,
            service=receipt.service,
            status=receipt.status,
            external_reference=receipt.external_reference,
            submitted_at=receipt.submitted_at,
            message=receipt.message,
            transaction_id=transaction.transaction_id,
            idempotency_key=key,
        )

    def get_transaction(self, transaction_id: str) -> ServiceTransaction:
        try:
            return self._transactions[transaction_id]
        except KeyError as exc:
            raise ExecutionError(f"unknown transaction: {transaction_id}") from exc

    def update_status(
        self,
        transaction_id: str,
        status: ExecutionStatus,
        *,
        external_reference: str | None = None,
        message: str = "",
    ) -> ServiceTransaction:
        transaction = self.get_transaction(transaction_id)
        transaction.transition(
            status,
            external_reference=external_reference,
            message=message,
        )
        return transaction

    def _receipt_from_transaction(
        self,
        transaction: ServiceTransaction,
        service: str,
    ) -> ExecutionReceipt:
        return ExecutionReceipt(
            action_id=transaction.action_id,
            service=service,
            status=transaction.status,
            external_reference=transaction.external_reference,
            submitted_at=transaction.events[0].occurred_at,
            message="Replayed idempotent submission.",
            transaction_id=transaction.transaction_id,
            idempotency_key=transaction.idempotency_key,
        )


class MockServiceAdapter:
    """Deterministic adapter; it never contacts an external service."""

    def __init__(self, name: str):
        self.name = name

    def execute(self, request: ExecutionRequest) -> ExecutionReceipt:
        return ExecutionReceipt(
            action_id=request.action.id,
            service=self.name,
            status="submitted",
            external_reference=f"mock:{request.action.id}",
            submitted_at=datetime.now(timezone.utc),
            message="Accepted by mock adapter; no external service was contacted.",
            transaction_id="",
            idempotency_key=request.idempotency_key,
        )
