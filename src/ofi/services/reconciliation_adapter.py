"""Provider-facing adapter for authenticated reconciliation responses.

The adapter fetches one exact attempt's reconciliation result and verifies it.
It deliberately does not mutate transaction state, persist event IDs, or retry.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit, urlunsplit
from urllib.request import Request, urlopen

from ofi.services.reconciliation import (
    ReconciliationError,
    ReconciliationEvidence,
    ReconciliationStatus,
    verify_reconciliation_evidence,
)


class ReconciliationTransportError(RuntimeError):
    """Provider reconciliation endpoint could not return a usable response."""


@dataclass(frozen=True)
class SignedReconciliationResponse:
    raw_payload: bytes
    signature: str


class ReconciliationTransport(Protocol):
    """Transport contract for a provider's authoritative reconciliation endpoint."""

    def fetch(self, *, transaction_id: str, attempt_id: str) -> SignedReconciliationResponse:
        ...


class HttpsReconciliationTransport:
    """Minimal HTTPS GET transport for a configured provider endpoint.

    The endpoint is configuration, never user input. Only transaction and attempt
    identifiers are added as query parameters. The provider signs the exact response
    bytes and returns the hex HMAC-SHA256 in X-OFI-Signature.
    """

    def __init__(self, endpoint_url: str, *, timeout_seconds: float = 5.0):
        parsed = urlsplit(endpoint_url)
        if parsed.scheme != "https" or not parsed.hostname:
            raise ValueError("reconciliation endpoint must be an absolute HTTPS URL")
        if parsed.username or parsed.password or parsed.fragment:
            raise ValueError("endpoint URL must not contain credentials or a fragment")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self.endpoint_url = endpoint_url
        self.timeout_seconds = timeout_seconds

    def fetch(self, *, transaction_id: str, attempt_id: str) -> SignedReconciliationResponse:
        parsed = urlsplit(self.endpoint_url)
        query = parsed.query
        attempt_query = urlencode(
            {"transaction_id": transaction_id, "attempt_id": attempt_id}
        )
        combined_query = f"{query}&{attempt_query}" if query else attempt_query
        url = urlunsplit(
            (parsed.scheme, parsed.netloc, parsed.path, combined_query, "")
        )
        request = Request(
            url,
            headers={"Accept": "application/json", "User-Agent": "OpenFarmIntelligence/0.1"},
            method="GET",
        )
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                raw_payload = response.read()
                signature = response.headers.get("X-OFI-Signature", "")
        except (HTTPError, URLError, TimeoutError, OSError) as exc:
            raise ReconciliationTransportError(
                "provider reconciliation request failed"
            ) from exc

        if not signature:
            raise ReconciliationTransportError(
                "provider response is missing X-OFI-Signature"
            )
        return SignedReconciliationResponse(raw_payload, signature)


@dataclass(frozen=True)
class ProviderReconciliationAdapter:
    provider_id: str
    provider_secret: str
    transport: ReconciliationTransport

    def reconcile(
        self,
        *,
        transaction_id: str,
        attempt_id: str,
        now: datetime | None = None,
    ) -> ReconciliationEvidence:
        """Fetch and verify authoritative evidence for the exact execution attempt."""
        if not self.provider_id.strip() or not self.provider_secret:
            raise ValueError("provider identity and secret are required")
        if not transaction_id.strip() or not attempt_id.strip():
            raise ValueError("transaction_id and attempt_id are required")

        response = self.transport.fetch(
            transaction_id=transaction_id,
            attempt_id=attempt_id,
        )
        try:
            payload = json.loads(response.raw_payload)
            if not isinstance(payload, dict):
                raise ValueError("payload must be an object")
            checked_at = datetime.fromisoformat(payload["checked_at"])
            evidence = ReconciliationEvidence(
                provider_id=payload["provider_id"],
                transaction_id=payload["transaction_id"],
                attempt_id=payload["attempt_id"],
                event_id=payload["event_id"],
                status=payload["status"],
                checked_at=checked_at,
                external_reference=payload.get("external_reference"),
                message=payload.get("message", ""),
            )
        except (UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            raise ReconciliationError("invalid provider reconciliation payload schema") from exc

        if evidence.status not in {"executed", "not_executed", "pending", "unknown"}:
            raise ReconciliationError("unsupported reconciliation status")

        return verify_reconciliation_evidence(
            evidence,
            raw_payload=response.raw_payload,
            signature=response.signature,
            provider_secret=self.provider_secret,
            expected_provider_id=self.provider_id,
            expected_transaction_id=transaction_id,
            expected_attempt_id=attempt_id,
            now=now,
        )
