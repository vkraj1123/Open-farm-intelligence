from dataclasses import asdict
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json

import pytest

from ofi.services.reconciliation import ReconciliationError
from ofi.services.reconciliation_adapter import (
    HttpsReconciliationTransport,
    ProviderReconciliationAdapter,
    ReconciliationTransportError,
    SignedReconciliationResponse,
)


SECRET = "provider-secret"
NOW = datetime(2026, 10, 10, 12, 0, tzinfo=timezone.utc)


class FakeTransport:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.calls = []

    def fetch(self, *, transaction_id, attempt_id):
        self.calls.append((transaction_id, attempt_id))
        if self.error:
            raise self.error
        return self.response


def signed_response(**overrides):
    payload = {
        "provider_id": "provider-1",
        "transaction_id": "txn-1",
        "attempt_id": "attempt-1",
        "event_id": "reconcile-event-1",
        "status": "not_executed",
        "checked_at": (NOW - timedelta(seconds=5)).isoformat(),
        "external_reference": None,
        "message": "No execution found.",
    }
    payload.update(overrides)
    raw = json.dumps(payload, separators=(",", ":")).encode()
    signature = hmac.new(SECRET.encode(), raw, hashlib.sha256).hexdigest()
    return SignedReconciliationResponse(raw, signature)


def adapter(transport):
    return ProviderReconciliationAdapter(
        provider_id="provider-1",
        provider_secret=SECRET,
        transport=transport,
    )


def test_adapter_fetches_and_verifies_exact_attempt():
    transport = FakeTransport(signed_response())
    result = adapter(transport).reconcile(
        transaction_id="txn-1", attempt_id="attempt-1", now=NOW
    )
    assert result.status == "not_executed"
    assert result.event_id == "reconcile-event-1"
    assert transport.calls == [("txn-1", "attempt-1")]


def test_adapter_returns_hash_of_exact_signed_response_bytes():
    response = signed_response()
    transport = FakeTransport(response)
    result, digest = adapter(transport).reconcile_with_digest(
        transaction_id="txn-1", attempt_id="attempt-1", now=NOW
    )
    assert result.status == "not_executed"
    assert digest == hashlib.sha256(response.raw_payload).hexdigest()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("provider_id", "provider-other"),
        ("transaction_id", "txn-other"),
        ("attempt_id", "attempt-other"),
    ],
)
def test_adapter_rejects_evidence_for_another_identity(field, value):
    transport = FakeTransport(signed_response(**{field: value}))
    with pytest.raises(ReconciliationError, match="different provider"):
        adapter(transport).reconcile(
            transaction_id="txn-1", attempt_id="attempt-1", now=NOW
        )


def test_adapter_rejects_bad_signature():
    response = signed_response()
    transport = FakeTransport(
        SignedReconciliationResponse(response.raw_payload, "0" * 64)
    )
    with pytest.raises(ReconciliationError, match="invalid provider"):
        adapter(transport).reconcile(
            transaction_id="txn-1", attempt_id="attempt-1", now=NOW
        )


def test_adapter_rejects_malformed_schema():
    raw = b'{"provider_id": ["not-a-string"]}'
    signature = hmac.new(SECRET.encode(), raw, hashlib.sha256).hexdigest()
    transport = FakeTransport(SignedReconciliationResponse(raw, signature))
    with pytest.raises(ReconciliationError, match="payload schema"):
        adapter(transport).reconcile(
            transaction_id="txn-1", attempt_id="attempt-1", now=NOW
        )


def test_transport_failure_propagates_without_fabricating_a_status():
    transport = FakeTransport(error=ReconciliationTransportError("provider offline"))
    with pytest.raises(ReconciliationTransportError, match="offline"):
        adapter(transport).reconcile(
            transaction_id="txn-1", attempt_id="attempt-1", now=NOW
        )


@pytest.mark.parametrize(
    "url",
    [
        "http://provider.example/reconcile",
        "https:///reconcile",
        "https://user:password@provider.example/reconcile",
        "https://provider.example/reconcile#fragment",
    ],
)
def test_https_transport_rejects_unsafe_endpoint_configuration(url):
    with pytest.raises(ValueError):
        HttpsReconciliationTransport(url)


def test_https_transport_requires_positive_timeout():
    with pytest.raises(ValueError, match="positive"):
        HttpsReconciliationTransport(
            "https://provider.example/reconcile", timeout_seconds=0
        )
