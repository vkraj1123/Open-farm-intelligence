from datetime import datetime, timedelta, timezone

import pytest

from ofi.services.execution_gateway import (
    ExecutionError,
    MockServiceAdapter,
    ServiceExecutionGateway,
)
from ofi.services.provider_callback import (
    CallbackVerificationError,
    ProviderCallback,
    ProviderCallbackVerifier,
)
from ofi.services.action_router import ActionRouter
from ofi.services.action_router import ActionRequest
from ofi.services.execution_gateway import ActorIdentity, ConsentGrant
from ofi.services.transaction_repository import InMemoryTransactionRepository


def _action():
    return ActionRequest(
        id="callback-action",
        case_id="case-1",
        farm_id="farm-1",
        action="REQUEST_TEST",
        service="soil_test",
        confidence=0.8,
        rationale="Verify soil condition.",
    )


def test_provider_callback_signature_and_freshness():
    now = datetime.now(timezone.utc)
    callback = ProviderCallback(
        event_id="evt-1",
        transaction_id="txn-1",
        provider_id="lab-01",
        status="completed",
        occurred_at=now,
        signature="",
        external_reference="lab-ref-1",
        message="Complete",
    )
    signed = ProviderCallback(**{**callback.__dict__, "signature": callback.sign("secret")})
    ProviderCallbackVerifier({"lab-01": "secret"}).verify(signed, as_of=now)

    with pytest.raises(CallbackVerificationError):
        ProviderCallbackVerifier({"lab-01": "wrong"}).verify(signed, as_of=now)

    stale = ProviderCallback(**{
        **signed.__dict__,
        "occurred_at": now - timedelta(hours=1),
        "signature": "",
    })
    stale = ProviderCallback(**{**stale.__dict__, "signature": stale.sign("secret")})
    with pytest.raises(CallbackVerificationError, match="stale"):
        ProviderCallbackVerifier({"lab-01": "secret"}).verify(stale, as_of=now)


def test_gateway_applies_callback_once():
    repo = InMemoryTransactionRepository()
    gateway = ServiceExecutionGateway(
        [MockServiceAdapter("soil_test", provider_id="lab-01")],
        transaction_repository=repo,
        callback_verifier=ProviderCallbackVerifier({"lab-01": "secret"}),
    )
    now = datetime.now(timezone.utc)
    receipt = gateway.submit(
        action=_action(),
        route=ActionRouter().route_action(_action()),
        actor=ActorIdentity("u1", "farmer"),
        consent=ConsentGrant(
            "u1", "soil_test", "granted", now, expires_at=now + timedelta(days=1)
        ),
        provider_id="lab-01",
    )
    callback = ProviderCallback(
        event_id="evt-1",
        transaction_id=receipt.transaction_id,
        provider_id="lab-01",
        status="completed",
        occurred_at=now,
        signature="",
        external_reference="lab-ref-1",
        message="Completed by lab",
    )
    callback = ProviderCallback(
        **{**callback.__dict__, "signature": callback.sign("secret")}
    )

    first = gateway.handle_callback(callback)
    second = gateway.handle_callback(callback)

    assert first.status == "completed"
    assert second.status == "completed"
    assert len(first.events) == 2
    assert len(second.events) == 2
    assert gateway.action_status(_action().id) == "completed"


def test_gateway_rejects_callback_from_wrong_provider():
    repo = InMemoryTransactionRepository()
    gateway = ServiceExecutionGateway(
        [MockServiceAdapter("soil_test", provider_id="lab-01")],
        transaction_repository=repo,
        callback_verifier=ProviderCallbackVerifier({"lab-02": "secret"}),
    )
    with pytest.raises(ExecutionError, match="unknown callback provider"):
        gateway.handle_callback(
            ProviderCallback(
                event_id="evt-1",
                transaction_id="txn-missing",
                provider_id="lab-02",
                status="completed",
                occurred_at=datetime.now(timezone.utc),
                signature="invalid",
            )
        )
