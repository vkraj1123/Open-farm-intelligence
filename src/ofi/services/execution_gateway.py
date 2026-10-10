from dataclasses import dataclass
import hashlib
import hmac
import json
from datetime import datetime, timezone
from typing import Literal, Protocol
from uuid import uuid4

from ofi.services.action_router import ActionRequest, ActionRoute
from ofi.services.service_transaction import (
    ExecutionAttempt,
    ServiceTransaction,
    TransactionEvent,
    action_status_for_transaction,
)
from ofi.services.transaction_repository import (
    InMemoryTransactionRepository,
    TransactionConflictError,
    TransactionRepository,
)


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
    provider_id: str
    transaction_id: str | None = None
    attempt_id: str | None = None


@dataclass(frozen=True)
class ExecutionReceipt:
    action_id: str
    service: str
    provider_id: str
    status: ExecutionStatus
    external_reference: str | None
    submitted_at: datetime
    message: str
    transaction_id: str
    idempotency_key: str
    attempt_id: str | None = None


class ServiceAdapter(Protocol):
    name: str
    provider_id: str

    def execute(self, request: ExecutionRequest) -> ExecutionReceipt:
        ...


class ExecutionError(Exception):
    pass


@dataclass(frozen=True)
class ProviderCallback:
    provider_id: str
    event_id: str
    transaction_id: str
    attempt_id: str
    status: ExecutionStatus
    external_reference: str | None = None
    message: str = ""


def verify_callback_signature(
    *, secret: str, payload: bytes, signature: str
) -> bool:
    expected = hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


class ServiceExecutionGateway:
    """Safe execution boundary with provider-specific routing and durable state."""

    def __init__(
        self,
        adapters: list[ServiceAdapter] | None = None,
        *,
        transaction_repository: TransactionRepository | None = None,
    ):
        self._adapters = {}
        self._service_adapters = {}
        for adapter in adapters or []:
            self.register(adapter)
        self._transactions = transaction_repository or InMemoryTransactionRepository()

    def register(self, adapter: ServiceAdapter) -> None:
        if adapter.provider_id in self._adapters:
            raise ValueError(f"provider adapter already registered: {adapter.provider_id}")
        self._adapters[adapter.provider_id] = adapter
        self._service_adapters.setdefault(adapter.name, adapter)

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
        if consent is not None and consent.actor_id != actor.actor_id:
            raise ExecutionError("consent actor does not match authenticated actor")
        if consent is not None and not consent.active():
            raise ExecutionError("consent is not active")

        adapter = self._adapters.get(provider_id) if provider_id else None
        if adapter is None:
            adapter = self._service_adapters.get(route.service)
        if adapter is None:
            raise ExecutionError(
                f"no execution adapter registered for provider={provider_id!r}, "
                f"service={route.service!r}"
            )

        selected_provider = provider_id or adapter.provider_id
        if provider_id and adapter.provider_id != provider_id:
            raise ExecutionError("provider adapter identity mismatch")

        key = idempotency_key or f"{action.id}:{selected_provider}"
        request_fingerprint = f"{action.id}|{route.service}|{selected_provider}"
        transaction_id = f"txn:{uuid4()}"
        transaction = ServiceTransaction(
            transaction_id=transaction_id,
            idempotency_key=key,
            request_fingerprint=request_fingerprint,
            action_id=action.id,
            provider_id=selected_provider,
            status="submitted",
            events=[
                TransactionEvent(
                    transaction_id=transaction_id,
                    status="submitted",
                    occurred_at=datetime.now(timezone.utc),
                    message="Execution submitted to provider boundary.",
                )
            ],
        )

        try:
            creation = self._transactions.create_if_absent(transaction)
        except TransactionConflictError as exc:
            raise ExecutionError(str(exc)) from exc

        if not creation.created:
            return self._receipt_from_transaction(
                creation.transaction, route.service
            )
        transaction = creation.transaction
        now = datetime.now(timezone.utc)
        attempt_id = f"attempt:{uuid4()}"
        self._transactions.create_attempt(
            ExecutionAttempt(
                attempt_id=attempt_id,
                transaction_id=transaction.transaction_id,
                attempt_number=1,
                provider_id=selected_provider,
                status="ready",
                created_at=now,
                updated_at=now,
            )
        )

        request = ExecutionRequest(
            action=action,
            route=route,
            actor=actor,
            consent=consent,
            idempotency_key=key,
            provider_id=selected_provider,
            transaction_id=transaction.transaction_id,
            attempt_id=attempt_id,
        )
        return self.dispatch_ready_attempt(attempt_id=attempt_id, request=request)

    def dispatch_ready_attempt(
        self, *, attempt_id: str, request: ExecutionRequest
    ) -> ExecutionReceipt:
        """Claim and dispatch one prepared attempt at most once locally.

        The durable claim is written before the network call. If the process
        crashes or transport fails after that point, the attempt is ambiguous
        and must be reconciled rather than dispatched again.
        """
        if not request.actor.authenticated:
            raise ExecutionError("authenticated actor is required")
        if request.consent is None and request.route.capability not in {"expert", "verification"}:
            raise ExecutionError("explicit consent is required for this service")
        if request.consent is not None and request.consent.actor_id != request.actor.actor_id:
            raise ExecutionError("consent actor does not match authenticated actor")
        if request.consent is not None and not request.consent.active():
            raise ExecutionError("consent is not active")

        if request.transaction_id is None or request.transaction_id == "":
            raise ExecutionError("transaction_id is required to dispatch a prepared attempt")
        if request.attempt_id != attempt_id:
            raise ExecutionError("request attempt does not match prepared attempt")
        try:
            transaction = self._transactions.get(request.transaction_id)
            attempt = next(
                item for item in self._transactions.list_attempts(request.transaction_id)
                if item.attempt_id == attempt_id
            )
        except (KeyError, StopIteration) as exc:
            raise ExecutionError("prepared attempt or transaction was not found") from exc

        if request.action.id != transaction.action_id:
            raise ExecutionError("request action does not match prepared transaction")
        if request.provider_id != attempt.provider_id or request.provider_id != transaction.provider_id:
            raise ExecutionError("request provider does not match prepared attempt")
        adapter = self._adapters.get(attempt.provider_id)
        if adapter is None or adapter.provider_id != attempt.provider_id:
            raise ExecutionError("no matching provider adapter registered for prepared attempt")
        if adapter.name != request.route.service:
            raise ExecutionError("provider adapter service does not match requested route")
        try:
            attempt = self._transactions.claim_attempt_for_dispatch(attempt_id)
        except (KeyError, TransactionConflictError) as exc:
            raise ExecutionError(str(exc)) from exc

        # Stable per-attempt identity is passed to the provider adapter so it
        # can use it as an external idempotency key if the provider supports it.
        dispatch_request = ExecutionRequest(
            action=request.action,
            route=request.route,
            actor=request.actor,
            consent=request.consent,
            idempotency_key=request.idempotency_key,
            provider_id=request.provider_id,
            transaction_id=transaction.transaction_id,
            attempt_id=attempt_id,
        )
        try:
            receipt = adapter.execute(dispatch_request)
        except Exception as exc:
            try:
                self._transactions.transition_attempt(
                    attempt_id, "unknown", last_error=str(exc)
                )
            except (KeyError, TransactionConflictError):
                pass
            raise ExecutionError(
                "provider execution outcome is unknown; reconcile before any retry"
            ) from exc

        if receipt.external_reference is not None:
            transaction = self._transactions.update_external_reference(
                transaction.transaction_id, receipt.external_reference,
            )

        # A callback may have advanced the attempt while execute() was in
        # flight. Do not overwrite that newer state with a stale sync receipt.
        current_attempt = next(
            item for item in self._transactions.list_attempts(transaction.transaction_id)
            if item.attempt_id == attempt_id
        )
        if current_attempt.status == "submitted":
            self._transactions.transition_attempt(
                attempt_id, receipt.status,
                external_reference=receipt.external_reference,
            )

        transaction = self._transactions.get(transaction.transaction_id)
        if receipt.status != "submitted" and transaction.status == "submitted":
            try:
                transaction = self._transactions.transition(
                    transaction.transaction_id, receipt.status,
                    external_reference=receipt.external_reference,
                    message=receipt.message,
                )
            except ValueError:
                # A concurrent callback may have advanced the lifecycle after
                # the read; reload authoritative state rather than regress it.
                transaction = self._transactions.get(transaction.transaction_id)
        else:
            transaction = self._transactions.get(transaction.transaction_id)

        return ExecutionReceipt(
            action_id=receipt.action_id,
            service=receipt.service,
            provider_id=attempt.provider_id,
            status=receipt.status,
            external_reference=receipt.external_reference,
            submitted_at=receipt.submitted_at,
            message=receipt.message,
            transaction_id=transaction.transaction_id,
            idempotency_key=request.idempotency_key,
            attempt_id=attempt_id,
        )

    def handle_provider_callback(
        self,
        callback: ProviderCallback,
        *,
        raw_payload: bytes,
        signature: str,
        provider_secret: str,
    ) -> ServiceTransaction:
        if not verify_callback_signature(
            secret=provider_secret,
            payload=raw_payload,
            signature=signature,
        ):
            raise ExecutionError("invalid provider callback signature")
        self._validate_callback_payload(callback, raw_payload)
        try:
            result = self._transactions.apply_callback(
                provider_id=callback.provider_id,
                event_id=callback.event_id,
                transaction_id=callback.transaction_id,
                attempt_id=callback.attempt_id,
                status=callback.status,
                external_reference=callback.external_reference,
                message=callback.message,
            )
        except (KeyError, TransactionConflictError) as exc:
            raise ExecutionError(str(exc)) from exc
        return result.transaction

    @staticmethod
    def _validate_callback_payload(callback: ProviderCallback, raw_payload: bytes) -> None:
        """Ensure the typed callback is exactly the data covered by the signature."""
        try:
            payload = json.loads(raw_payload)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ExecutionError("invalid provider callback payload") from exc
        if not isinstance(payload, dict):
            raise ExecutionError("invalid provider callback payload")

        required = {
            "provider_id": callback.provider_id,
            "event_id": callback.event_id,
            "transaction_id": callback.transaction_id,
            "attempt_id": callback.attempt_id,
            "status": callback.status,
        }
        optional = {
            "external_reference": callback.external_reference,
            "message": callback.message,
        }
        if any(payload.get(key) != value for key, value in required.items()):
            raise ExecutionError("provider callback fields do not match signed payload")
        if any(key in payload and payload[key] != value for key, value in optional.items()):
            raise ExecutionError("provider callback fields do not match signed payload")

    def action_status(self, action_id: str) -> str:
        matches = self._transactions.list_for_action(action_id)
        if not matches:
            raise ExecutionError(f"unknown action transaction: {action_id}")
        latest = max(matches, key=lambda tx: (tx.created_at, tx.transaction_id))
        return action_status_for_transaction(latest.status)

    def get_transaction(self, transaction_id: str) -> ServiceTransaction:
        try:
            return self._transactions.get(transaction_id)
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
        try:
            return self._transactions.transition(
                transaction_id,
                status,
                external_reference=external_reference,
                message=message,
            )
        except KeyError as exc:
            raise ExecutionError(f"unknown transaction: {transaction_id}") from exc

    def _receipt_from_transaction(
        self,
        transaction: ServiceTransaction,
        service: str,
    ) -> ExecutionReceipt:
        submitted_at = (
            transaction.events[0].occurred_at
            if transaction.events
            else transaction.created_at
        )
        return ExecutionReceipt(
            action_id=transaction.action_id,
            service=service,
            provider_id=transaction.provider_id or "",
            status=transaction.status,
            external_reference=transaction.external_reference,
            submitted_at=submitted_at,
            message="Replayed idempotent submission.",
            transaction_id=transaction.transaction_id,
            idempotency_key=transaction.idempotency_key,
            attempt_id=(self._transactions.list_attempts(transaction.transaction_id)[-1].attempt_id
                        if self._transactions.list_attempts(transaction.transaction_id) else None),
        )


class MockServiceAdapter:
    """Deterministic adapter; it never contacts an external service."""

    def __init__(self, name: str, provider_id: str | None = None):
        self.name = name
        self.provider_id = provider_id or f"mock-provider:{name}"

    def execute(self, request: ExecutionRequest) -> ExecutionReceipt:
        return ExecutionReceipt(
            action_id=request.action.id,
            service=self.name,
            provider_id=self.provider_id,
            status="submitted",
            external_reference=f"mock:{request.action.id}",
            submitted_at=datetime.now(timezone.utc),
            message="Accepted by mock adapter; no external service was contacted.",
            transaction_id="",
            idempotency_key=request.idempotency_key,
        )
