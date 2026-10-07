from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Literal, Protocol

from ofi.services.action_router import ActionRequest, ActionRoute


ConsentStatus = Literal["granted", "denied", "expired", "not_required"]
ExecutionStatus = Literal["submitted", "accepted", "rejected", "failed", "completed"]


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
        if self.expires_at is not None and self.expires_at <= moment:
            return False
        return True


@dataclass(frozen=True)
class ExecutionRequest:
    action: ActionRequest
    route: ActionRoute
    actor: ActorIdentity
    consent: ConsentGrant | None


@dataclass(frozen=True)
class ExecutionReceipt:
    action_id: str
    service: str
    status: ExecutionStatus
    external_reference: str | None
    submitted_at: datetime
    message: str


class ServiceAdapter(Protocol):
    name: str

    def execute(self, request: ExecutionRequest) -> ExecutionReceipt:
        ...


class ExecutionError(Exception):
    pass


class ServiceExecutionGateway:
    """Safe boundary for real external execution.

    Authentication/consent is checked before any adapter is invoked.
    Adapters remain replaceable and may connect to VISTAAR or other services.
    """

    def __init__(self, adapters: list[ServiceAdapter] | None = None):
        self._adapters = {adapter.name: adapter for adapter in adapters or []}

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
    ) -> ExecutionReceipt:
        if not actor.authenticated:
            raise ExecutionError("authenticated actor is required")
        if consent is None and route.capability not in {"expert", "verification"}:
            raise ExecutionError("explicit consent is required for this service")
        if consent is not None and not consent.active():
            raise ExecutionError("consent is not active")

        adapter = self._adapters.get(route.service)
        if adapter is None:
            raise ExecutionError(f"no execution adapter registered: {route.service}")

        request = ExecutionRequest(
            action=action,
            route=route,
            actor=actor,
            consent=consent,
        )
        return adapter.execute(request)


class MockServiceAdapter:
    """Deterministic adapter for integration tests and local development."""

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
        )
