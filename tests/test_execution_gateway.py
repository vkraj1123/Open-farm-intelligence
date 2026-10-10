from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json

import pytest

from ofi.services.action_router import ActionRequest, ActionRouter
from ofi.services.execution_gateway import (
    ActorIdentity,
    ConsentGrant,
    ExecutionError,
    ExecutionRequest,
    MockServiceAdapter,
    ProviderCallback,
    ServiceExecutionGateway,
)
from ofi.services.service_directory import ServiceCapability, ServiceDirectory, ServiceProvider
from ofi.services.service_transaction import ExecutionAttempt, ServiceTransaction
from ofi.services.transaction_repository import InMemoryTransactionRepository
from ofi.services.service_orchestrator import ServiceOrchestrator


def action():
    return ActionRequest(
        id="case-1-action-1",
        case_id="case-1",
        farm_id="farm-1",
        action="REQUEST_TEST",
        service="soil_test",
        confidence=0.8,
        rationale="Verify soil condition.",
    )


def route():
    return ActionRouter().route_action(action())


def consent():
    return ConsentGrant(
        "u1", "soil_test", "granted", datetime.now(timezone.utc),
        expires_at=datetime.now(timezone.utc) + timedelta(days=1),
    )


def test_execution_requires_authentication():
    gateway = ServiceExecutionGateway([MockServiceAdapter("soil_test")])
    with pytest.raises(ExecutionError):
        gateway.submit(
            action=action(), route=route(),
            actor=ActorIdentity("u1", "farmer", authenticated=False),
            consent=consent(),
        )


def test_execution_requires_active_consent_for_service():
    gateway = ServiceExecutionGateway([MockServiceAdapter("soil_test")])
    with pytest.raises(ExecutionError):
        gateway.submit(action=action(), route=route(), actor=ActorIdentity("u1", "farmer"))


def test_expired_consent_is_rejected():
    gateway = ServiceExecutionGateway([MockServiceAdapter("soil_test")])
    with pytest.raises(ExecutionError):
        gateway.submit(
            action=action(), route=route(), actor=ActorIdentity("u1", "farmer"),
            consent=ConsentGrant(
                "u1", "soil_test", "granted",
                datetime.now(timezone.utc) - timedelta(days=2),
                expires_at=datetime.now(timezone.utc) - timedelta(days=1),
            ),
        )


def test_mock_adapter_returns_provider_aware_receipt():
    gateway = ServiceExecutionGateway([
        MockServiceAdapter("soil_test", provider_id="lab-01")
    ])
    receipt = gateway.submit(
        action=action(), route=route(),
        actor=ActorIdentity("u1", "farmer"),
        consent=consent(),
        provider_id="lab-01",
    )
    assert receipt.status == "submitted"
    assert receipt.provider_id == "lab-01"
    assert receipt.external_reference == "mock:case-1-action-1"


def test_provider_discovery_identity_reaches_execution():
    directory = ServiceDirectory([
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
        ServiceProvider(
            provider_id="lab-02",
            name="Other Soil Lab",
            capabilities=(
                ServiceCapability(
                    service="soil_test",
                    capability="soil_testing",
                    action_types=frozenset({"REQUEST_TEST"}),
                ),
            ),
        ),
    ])
    selection = ServiceOrchestrator(directory).select(action())
    gateway = ServiceExecutionGateway([
        MockServiceAdapter("soil_test", provider_id=selection.provider_id)
    ])
    receipt = gateway.submit(
        action=action(), route=route(),
        actor=ActorIdentity("u1", "farmer"),
        consent=consent(),
        provider_id=selection.provider_id,
    )
    assert receipt.provider_id == "lab-01"


def test_unregistered_service_or_provider_is_not_silently_executed():
    gateway = ServiceExecutionGateway()
    with pytest.raises(ExecutionError):
        gateway.submit(
            action=action(), route=route(), actor=ActorIdentity("u1", "farmer"),
            consent=consent(), provider_id="missing-provider",
        )


def test_action_status_uses_explicit_transaction_creation_time():
    repo = InMemoryTransactionRepository()
    gateway = ServiceExecutionGateway(
        [MockServiceAdapter("soil_test", provider_id="lab-01")],
        transaction_repository=repo,
    )
    first = ServiceTransaction(
        transaction_id="txn-old",
        idempotency_key="attempt-1",
        action_id=action().id,
        provider_id="lab-01",
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    second = ServiceTransaction(
        transaction_id="txn-new",
        idempotency_key="attempt-2",
        action_id=action().id,
        provider_id="lab-01",
        created_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
    )
    repo.create(first)
    repo.create(second)
    repo.transition(first.transaction_id, "submitted")
    repo.transition(first.transaction_id, "accepted")
    repo.transition(first.transaction_id, "in_progress")
    repo.transition(first.transaction_id, "completed")
    repo.transition(second.transaction_id, "submitted")

    assert gateway.action_status(action().id) == "routed"


def test_idempotency_key_cannot_be_reused_for_different_request():
    gateway = ServiceExecutionGateway([
        MockServiceAdapter("soil_test", provider_id="lab-01"),
        MockServiceAdapter("soil_test", provider_id="lab-02"),
    ])
    actor = ActorIdentity("u1", "farmer")
    gateway.submit(
        action=action(), route=route(), actor=actor, consent=consent(),
        provider_id="lab-01", idempotency_key="same-key",
    )
    with pytest.raises(ExecutionError, match="idempotency key was reused"):
        gateway.submit(
            action=action(), route=route(), actor=actor, consent=consent(),
            provider_id="lab-02", idempotency_key="same-key",
        )


def test_provider_callback_requires_valid_signature_and_is_idempotent():
    gateway = ServiceExecutionGateway([MockServiceAdapter("soil_test")])
    kwargs = dict(
        action=action(),
        route=ActionRouter().route_action(action()),
        actor=ActorIdentity("u1", "farmer"),
        consent=ConsentGrant(
            "u1", "soil_test", "granted", datetime.now(timezone.utc),
            expires_at=datetime.now(timezone.utc) + timedelta(days=1),
        ),
        idempotency_key="callback-test",
    )
    receipt = gateway.submit(**kwargs)
    callback = ProviderCallback(
        provider_id=receipt.provider_id,
        event_id="evt-1",
        transaction_id=receipt.transaction_id,
        attempt_id=receipt.attempt_id,
        status="accepted",
    )
    raw = json.dumps({
        "provider_id": callback.provider_id,
        "event_id": callback.event_id,
        "transaction_id": callback.transaction_id,
        "attempt_id": callback.attempt_id,
        "status": callback.status,
    }, separators=(",", ":")).encode()
    signature = hmac.new(b"secret", raw, hashlib.sha256).hexdigest()

    with pytest.raises(ExecutionError, match="invalid provider callback"):
        gateway.handle_provider_callback(
            callback, raw_payload=raw, signature="bad", provider_secret="secret"
        )

    first = gateway.handle_provider_callback(
        callback, raw_payload=raw, signature=signature, provider_secret="secret"
    )
    second = gateway.handle_provider_callback(
        callback, raw_payload=raw, signature=signature, provider_secret="secret"
    )

    assert first.status == "accepted"
    assert second.status == "accepted"
    assert len(second.events) == 2

    attempts = gateway._transactions.list_attempts(receipt.transaction_id)
    assert len(attempts) == 1
    assert attempts[0].attempt_id == receipt.attempt_id
    assert attempts[0].status == "accepted"


def test_signed_payload_cannot_be_used_with_different_typed_callback_fields():
    gateway = ServiceExecutionGateway([MockServiceAdapter("soil_test")])
    receipt = gateway.submit(
        action=action(), route=route(), actor=ActorIdentity("u1", "farmer"),
        consent=consent(), idempotency_key="payload-binding-test",
    )
    payload = {
        "provider_id": receipt.provider_id,
        "event_id": "evt-bound",
        "transaction_id": receipt.transaction_id,
        "attempt_id": receipt.attempt_id,
        "status": "accepted",
    }
    raw = json.dumps(payload, separators=(",", ":")).encode()
    signature = hmac.new(b"secret", raw, hashlib.sha256).hexdigest()
    callback = ProviderCallback(
        provider_id=receipt.provider_id,
        event_id="evt-other",
        transaction_id=receipt.transaction_id,
        attempt_id=receipt.attempt_id,
        status="accepted",
    )
    with pytest.raises(ExecutionError, match="do not match signed payload"):
        gateway.handle_provider_callback(
            callback, raw_payload=raw, signature=signature, provider_secret="secret"
        )


def test_callback_updates_named_attempt_not_latest_attempt():
    repo = InMemoryTransactionRepository()
    gateway = ServiceExecutionGateway(
        [MockServiceAdapter("soil_test")], transaction_repository=repo
    )
    receipt = gateway.submit(
        action=action(), route=route(), actor=ActorIdentity("u1", "farmer"),
        consent=consent(), idempotency_key="attempt-binding-test",
    )
    repo.transition_attempt(receipt.attempt_id, "unknown", last_error="timeout")
    from ofi.services.service_transaction import ExecutionAttempt
    now = datetime.now(timezone.utc)
    repo.create_attempt(ExecutionAttempt(
        attempt_id="attempt:newer", transaction_id=receipt.transaction_id,
        attempt_number=2, provider_id=receipt.provider_id, status="submitted",
        created_at=now, updated_at=now,
    ))
    callback = ProviderCallback(
        provider_id=receipt.provider_id, event_id="evt-old-attempt",
        transaction_id=receipt.transaction_id, attempt_id=receipt.attempt_id,
        status="accepted",
    )
    raw = json.dumps({
        "provider_id": callback.provider_id, "event_id": callback.event_id,
        "transaction_id": callback.transaction_id, "attempt_id": callback.attempt_id,
        "status": callback.status,
    }, separators=(",", ":")).encode()
    signature = hmac.new(b"secret", raw, hashlib.sha256).hexdigest()
    gateway.handle_provider_callback(
        callback, raw_payload=raw, signature=signature, provider_secret="secret"
    )
    attempts = repo.list_attempts(receipt.transaction_id)
    assert attempts[0].status == "accepted"
    assert attempts[1].status == "submitted"


def _prepared_dispatch(adapter=None, repo=None):
    adapter = adapter or MockServiceAdapter("soil_test", provider_id="lab-01")
    repo = repo or InMemoryTransactionRepository()
    tx = ServiceTransaction(
        transaction_id="txn-ready-dispatch",
        idempotency_key="dispatch-key",
        request_fingerprint="dispatch-fingerprint",
        action_id=action().id,
        provider_id="lab-01",
        status="submitted",
    )
    repo.create(tx)
    now = datetime.now(timezone.utc)
    repo.create_attempt(ExecutionAttempt(
        attempt_id="attempt-ready-dispatch",
        transaction_id=tx.transaction_id,
        attempt_number=1,
        provider_id="lab-01",
        status="ready",
        created_at=now,
        updated_at=now,
    ))
    request = ExecutionRequest(
        action=action(),
        route=route(),
        actor=ActorIdentity("u1", "farmer"),
        consent=consent(),
        idempotency_key=tx.idempotency_key,
        provider_id="lab-01",
        transaction_id=tx.transaction_id,
        attempt_id="attempt-ready-dispatch",
    )
    return ServiceExecutionGateway([adapter], transaction_repository=repo), repo, adapter, request


def test_ready_attempt_is_claimed_before_dispatch_and_cannot_be_dispatched_twice():
    class CountingAdapter(MockServiceAdapter):
        calls = 0

        def execute(self, request):
            self.calls += 1
            assert request.attempt_id == "attempt-ready-dispatch"
            assert request.transaction_id == "txn-ready-dispatch"
            return super().execute(request)

    gateway, repo, adapter, request = _prepared_dispatch(CountingAdapter("soil_test", "lab-01"))
    receipt = gateway.dispatch_ready_attempt(attempt_id="attempt-ready-dispatch", request=request)
    assert receipt.status == "submitted"
    assert adapter.calls == 1
    assert repo.list_attempts("txn-ready-dispatch")[0].status == "submitted"
    with pytest.raises(ExecutionError, match="cannot be dispatched from status"):
        gateway.dispatch_ready_attempt(attempt_id="attempt-ready-dispatch", request=request)
    assert adapter.calls == 1


def test_dispatch_transport_exception_marks_attempt_unknown_and_blocks_resend():
    class CrashAdapter(MockServiceAdapter):
        calls = 0

        def execute(self, request):
            self.calls += 1
            raise RuntimeError("worker lost connection after send")

    gateway, repo, adapter, request = _prepared_dispatch(CrashAdapter("soil_test", "lab-01"))
    with pytest.raises(ExecutionError, match="outcome is unknown"):
        gateway.dispatch_ready_attempt(attempt_id="attempt-ready-dispatch", request=request)
    assert repo.list_attempts("txn-ready-dispatch")[0].status == "unknown"
    with pytest.raises(ExecutionError, match="cannot be dispatched from status"):
        gateway.dispatch_ready_attempt(attempt_id="attempt-ready-dispatch", request=request)
    assert adapter.calls == 1


def test_concurrent_dispatch_claims_only_one_worker():
    from concurrent.futures import ThreadPoolExecutor

    class CountingAdapter(MockServiceAdapter):
        calls = 0

        def execute(self, request):
            self.calls += 1
            return super().execute(request)

    gateway, repo, adapter, request = _prepared_dispatch(CountingAdapter("soil_test", "lab-01"))

    def dispatch(_):
        try:
            return gateway.dispatch_ready_attempt(attempt_id="attempt-ready-dispatch", request=request)
        except ExecutionError:
            return None

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(dispatch, range(8)))
    assert sum(item is not None for item in results) == 1
    assert adapter.calls == 1
    assert repo.list_attempts("txn-ready-dispatch")[0].status == "submitted"

