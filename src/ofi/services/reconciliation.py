"""Authenticated, attempt-scoped provider reconciliation evidence.

This module verifies evidence; it does not contact providers or schedule retries.
A provider adapter must obtain the evidence from an authoritative provider endpoint.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
from typing import Literal


ReconciliationStatus = Literal["executed", "not_executed", "pending", "unknown"]


class ReconciliationError(ValueError):
    """Raised when provider reconciliation evidence is invalid or unsafe to use."""


@dataclass(frozen=True)
class ReconciliationEvidence:
    provider_id: str
    transaction_id: str
    attempt_id: str
    event_id: str
    status: ReconciliationStatus
    checked_at: datetime
    external_reference: str | None = None
    message: str = ""


def verify_reconciliation_evidence(
    evidence: ReconciliationEvidence,
    *,
    raw_payload: bytes,
    signature: str,
    provider_secret: str,
    expected_provider_id: str,
    expected_transaction_id: str,
    expected_attempt_id: str,
    now: datetime | None = None,
    max_age: timedelta = timedelta(minutes=5),
    allowed_future_skew: timedelta = timedelta(seconds=30),
) -> ReconciliationEvidence:
    """Authenticate and validate evidence for one exact attempt.

    The signature is HMAC-SHA256 over the original bytes. The decoded fields
    must exactly match the typed evidence and expected attempt identity.
    Evidence outside the freshness window is rejected to limit replay risk.
    """

    if not provider_secret:
        raise ReconciliationError("provider reconciliation secret is required")
    if max_age <= timedelta(0) or allowed_future_skew < timedelta(0):
        raise ValueError("freshness limits must be positive/non-negative")

    expected_signature = hmac.new(
        provider_secret.encode("utf-8"), raw_payload, hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(expected_signature, signature):
        raise ReconciliationError("invalid provider reconciliation signature")

    try:
        payload = json.loads(raw_payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReconciliationError("invalid provider reconciliation payload") from exc
    if not isinstance(payload, dict):
        raise ReconciliationError("invalid provider reconciliation payload")

    checked_at = evidence.checked_at
    if checked_at.tzinfo is None or checked_at.utcoffset() is None:
        raise ReconciliationError("checked_at must be timezone-aware")
    checked_at = checked_at.astimezone(timezone.utc)

    expected_fields = {
        "provider_id": evidence.provider_id,
        "transaction_id": evidence.transaction_id,
        "attempt_id": evidence.attempt_id,
        "event_id": evidence.event_id,
        "status": evidence.status,
        "checked_at": evidence.checked_at.isoformat(),
        "external_reference": evidence.external_reference,
        "message": evidence.message,
    }
    if any(payload.get(key) != value for key, value in expected_fields.items()):
        raise ReconciliationError(
            "typed reconciliation evidence does not match signed payload"
        )

    expected_identity = (
        expected_provider_id,
        expected_transaction_id,
        expected_attempt_id,
    )
    evidence_identity = (
        evidence.provider_id,
        evidence.transaction_id,
        evidence.attempt_id,
    )
    if evidence_identity != expected_identity:
        raise ReconciliationError(
            "reconciliation evidence belongs to a different provider, transaction or attempt"
        )

    if evidence.status not in {"executed", "not_executed", "pending", "unknown"}:
        raise ReconciliationError("unsupported reconciliation status")
    if not evidence.event_id.strip():
        raise ReconciliationError("reconciliation event_id is required")

    moment = (now or datetime.now(timezone.utc))
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    moment = moment.astimezone(timezone.utc)
    if checked_at > moment + allowed_future_skew:
        raise ReconciliationError("reconciliation evidence timestamp is in the future")
    if moment - checked_at > max_age:
        raise ReconciliationError("reconciliation evidence is stale")

    return evidence
