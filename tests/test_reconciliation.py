from dataclasses import asdict
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json

import pytest

from ofi.services.reconciliation import (
    ReconciliationError,
    ReconciliationEvidence,
    verify_reconciliation_evidence,
)


SECRET = "provider-secret"
NOW = datetime(2026, 10, 10, 12, 0, tzinfo=timezone.utc)


def evidence(**overrides):
    values = {
        "provider_id": "provider-1",
        "transaction_id": "txn-1",
        "attempt_id": "attempt-1",
        "event_id": "reconcile-evt-1",
        "status": "not_executed",
        "checked_at": NOW - timedelta(seconds=10),
        "external_reference": None,
        "message": "No matching execution found.",
    }
    values.update(overrides)
    return ReconciliationEvidence(**values)


def signed_payload(item):
    payload = asdict(item)
    payload["checked_at"] = item.checked_at.isoformat()
    raw = json.dumps(payload, separators=(",", ":")).encode()
    signature = hmac.new(SECRET.encode(), raw, hashlib.sha256).hexdigest()
    return raw, signature


def verify(item, *, now=NOW, **overrides):
    raw, signature = signed_payload(item)
    return verify_reconciliation_evidence(
        item,
        raw_payload=raw,
        signature=signature,
        provider_secret=SECRET,
        expected_provider_id="provider-1",
        expected_transaction_id="txn-1",
        expected_attempt_id="attempt-1",
        now=now,
        **overrides,
    )


def test_valid_fresh_evidence_is_accepted_for_exact_attempt():
    item = evidence()
    assert verify(item) == item


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("provider_id", "provider-other"),
        ("transaction_id", "txn-other"),
        ("attempt_id", "attempt-other"),
    ],
)
def test_evidence_for_different_identity_is_rejected(field, value):
    with pytest.raises(ReconciliationError, match="different provider"):
        verify(evidence(**{field: value}))


def test_typed_evidence_cannot_disagree_with_signed_payload():
    item = evidence()
    raw, signature = signed_payload(item)
    tampered = evidence(event_id="different-event")
    with pytest.raises(ReconciliationError, match="does not match signed payload"):
        verify_reconciliation_evidence(
            tampered,
            raw_payload=raw,
            signature=signature,
            provider_secret=SECRET,
            expected_provider_id="provider-1",
            expected_transaction_id="txn-1",
            expected_attempt_id="attempt-1",
            now=NOW,
        )


def test_invalid_signature_is_rejected():
    item = evidence()
    raw, _ = signed_payload(item)
    with pytest.raises(ReconciliationError, match="invalid provider"):
        verify_reconciliation_evidence(
            item,
            raw_payload=raw,
            signature="0" * 64,
            provider_secret=SECRET,
            expected_provider_id="provider-1",
            expected_transaction_id="txn-1",
            expected_attempt_id="attempt-1",
            now=NOW,
        )


def test_stale_evidence_is_rejected():
    item = evidence(checked_at=NOW - timedelta(minutes=6))
    with pytest.raises(ReconciliationError, match="stale"):
        verify(item)


def test_future_evidence_outside_clock_skew_is_rejected():
    item = evidence(checked_at=NOW + timedelta(minutes=1))
    with pytest.raises(ReconciliationError, match="in the future"):
        verify(item)


def test_naive_timestamp_is_rejected():
    item = evidence(checked_at=datetime(2026, 10, 10, 11, 59))
    with pytest.raises(ReconciliationError, match="timezone-aware"):
        verify(item)


def test_signed_payload_must_be_valid_json():
    item = evidence()
    raw = b"not-json"
    signature = hmac.new(SECRET.encode(), raw, hashlib.sha256).hexdigest()
    with pytest.raises(ReconciliationError, match="invalid provider reconciliation payload"):
        verify_reconciliation_evidence(
            item,
            raw_payload=raw,
            signature=signature,
            provider_secret=SECRET,
            expected_provider_id="provider-1",
            expected_transaction_id="txn-1",
            expected_attempt_id="attempt-1",
            now=NOW,
        )
