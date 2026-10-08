from datetime import datetime, timezone

import pytest

from ofi.services.execution_gateway import (
    ActorIdentity,
    ConsentGrant,
    ExecutionError,
    MockServiceAdapter,
    ServiceExecutionGateway,
)
from ofi.services.action_router import ActionRequest, ActionRouter
from ofi.services.service_transaction import ServiceTransaction
from ofi.services.transaction_repository import (
    InMemoryTransactionRepository,
    TransactionConflictError,
)


def _action() -> ActionRequest:
    return ActionRequest(
        id="case-1-action-1",
        case_id="case-1",
        farm_id="farm-1",
        action="REQUEST_TEST",
        service="soil_test",
        confidence=0.8,
        rationale="Verify soil condition.",
    )


def _route():
    return ActionRouter().route_action(_action())


def _consent() -> ConsentGrant:
    now = datetime.now(timezone.utc)
    return ConsentGrant("u1", "soil_test", "granted", now)


def test_in_memory_repository_round_trips_transaction_and_events():
    repo = InMemoryTransactionRepository()
    tx = ServiceTransaction(
        transaction_id="txn-1",
        idempotency_key="key-1",
        request_fingerprint="fingerprint-1",
        action_id="action-1",
        provider_id="lab-01",
    )

    created = repo.create(tx)
    transitioned = repo.transition(created.transaction.transaction_id, "submitted", message="sent")

    assert transitioned.status == "submitted"
    fresh = repo.get("txn-1")
    assert fresh.status == "submitted"
    assert len(fresh.events) == 1
    assert fresh.events[0].status == "submitted"
    assert fresh.events[0].message == "sent"


def test_in_memory_repository_rejects_idempotency_fingerprint_conflict():
    repo = InMemoryTransactionRepository()
    repo.create(
        ServiceTransaction(
            transaction_id="txn-1",
            idempotency_key="same",
            request_fingerprint="fingerprint-1",
            action_id="action-1",
            provider_id="lab-01",
        )
    )

    with pytest.raises(TransactionConflictError, match="idempotency key was reused"):
        repo.create(
            ServiceTransaction(
                transaction_id="txn-2",
                idempotency_key="same",
                request_fingerprint="fingerprint-2",
                action_id="action-1",
                provider_id="lab-02",
            )
        )


def test_gateway_replays_from_repository_without_reexecuting_adapter():
    class CountingAdapter(MockServiceAdapter):
        calls = 0

        def execute(self, request):
            self.calls += 1
            return super().execute(request)

    adapter = CountingAdapter("soil_test", provider_id="lab-01")
    gateway = ServiceExecutionGateway(
        [adapter],
        transaction_repository=InMemoryTransactionRepository(),
    )
    actor = ActorIdentity("u1", "farmer")

    first = gateway.submit(
        action=_action(),
        route=_route(),
        actor=actor,
        consent=_consent(),
        provider_id="lab-01",
        idempotency_key="same-key",
    )
    second = gateway.submit(
        action=_action(),
        route=_route(),
        actor=actor,
        consent=_consent(),
        provider_id="lab-01",
        idempotency_key="same-key",
    )

    assert adapter.calls == 1
    assert second.transaction_id == first.transaction_id
    assert second.status == "submitted"


def test_gateway_uses_actual_provider_in_fingerprint_when_provider_is_implicit():
    first = MockServiceAdapter("soil_test", provider_id="lab-01")
    second = MockServiceAdapter("soil_test", provider_id="lab-02")
    repo = InMemoryTransactionRepository()
    gateway = ServiceExecutionGateway(
        [first, second],
        transaction_repository=repo,
    )

    gateway.submit(
        action=_action(),
        route=_route(),
        actor=ActorIdentity("u1", "farmer"),
        consent=_consent(),
        idempotency_key="same-key",
    )

    # Registration order chooses lab-01. A new gateway with the same key but
    # a different selected provider must not replay the old transaction.
    other_gateway = ServiceExecutionGateway(
        [second],
        transaction_repository=repo,
    )
    with pytest.raises(ExecutionError, match="idempotency key was reused"):
        other_gateway.submit(
            action=_action(),
            route=_route(),
            actor=ActorIdentity("u1", "farmer"),
            consent=_consent(),
            idempotency_key="same-key",
        )
