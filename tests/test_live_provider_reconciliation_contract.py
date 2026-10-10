"""Opt-in, read-only contract checks against a real reconciliation provider.

This test is skipped in ordinary CI unless all OFI_STAGING_* variables are set.
It only performs HTTPS GET reconciliation reads; it never dispatches work or
mutates a transaction repository. Use provider-issued staging attempts only.
"""
from __future__ import annotations

import json
import os
import re

import pytest

from ofi.services.reconciliation_adapter import (
    HttpsReconciliationTransport,
    ProviderReconciliationAdapter,
)


PROVIDER_ID = os.environ.get("OFI_STAGING_PROVIDER_ID", "").strip()
ENDPOINT = os.environ.get("OFI_STAGING_RECONCILIATION_ENDPOINT", "").strip()
SECRET = os.environ.get("OFI_STAGING_RECONCILIATION_SECRET", "")
RAW_CASES = os.environ.get("OFI_STAGING_RECONCILIATION_CASES", "").strip()
REQUIRED_STATUSES = {"executed", "not_executed", "pending", "unknown"}
REQUIRED_SETTINGS_PRESENT = bool(PROVIDER_ID and ENDPOINT and SECRET and RAW_CASES)


def _load_cases() -> list[dict[str, str]]:
    if not RAW_CASES:
        return []
    try:
        cases = json.loads(RAW_CASES)
    except json.JSONDecodeError as exc:
        raise ValueError("OFI_STAGING_RECONCILIATION_CASES must be valid JSON") from exc
    if not isinstance(cases, list) or not cases:
        raise ValueError("staging reconciliation cases must be a non-empty JSON array")
    normalized: list[dict[str, str]] = []
    for case in cases:
        if not isinstance(case, dict):
            raise ValueError("each staging reconciliation case must be an object")
        transaction_id = case.get("transaction_id")
        attempt_id = case.get("attempt_id")
        expected_status = case.get("expected_status")
        if not all(isinstance(value, str) and value.strip() for value in (
            transaction_id, attempt_id, expected_status
        )):
            raise ValueError(
                "each case requires non-empty transaction_id, attempt_id and expected_status"
            )
        if expected_status not in REQUIRED_STATUSES:
            raise ValueError("expected_status must be one of the four OFI reconciliation statuses")
        normalized.append({
            "transaction_id": transaction_id,
            "attempt_id": attempt_id,
            "expected_status": expected_status,
        })
    identities = [(item["transaction_id"], item["attempt_id"]) for item in normalized]
    if len(identities) != len(set(identities)):
        raise ValueError("staging cases must use unique transaction/attempt identities")
    if {item["expected_status"] for item in normalized} != REQUIRED_STATUSES:
        raise ValueError(
            "staging cases must cover executed, not_executed, pending and unknown"
        )
    return normalized


@pytest.mark.skipif(
    not REQUIRED_SETTINGS_PRESENT,
    reason="requires explicit read-only real-provider staging configuration",
)
def test_real_provider_signed_reconciliation_contract():
    """Verify signed identity, freshness and all four statuses without state mutation."""
    cases = _load_cases()
    transport = HttpsReconciliationTransport(ENDPOINT, timeout_seconds=5.0)
    adapter = ProviderReconciliationAdapter(
        provider_id=PROVIDER_ID,
        provider_secret=SECRET,
        transport=transport,
    )

    observed_statuses: set[str] = set()
    for case in cases:
        evidence, payload_digest = adapter.reconcile_with_digest(
            transaction_id=case["transaction_id"],
            attempt_id=case["attempt_id"],
        )
        assert evidence.provider_id == PROVIDER_ID
        assert evidence.transaction_id == case["transaction_id"]
        assert evidence.attempt_id == case["attempt_id"]
        assert evidence.status == case["expected_status"]
        assert re.fullmatch(r"[0-9a-f]{64}", payload_digest)
        observed_statuses.add(evidence.status)

    assert observed_statuses == REQUIRED_STATUSES
