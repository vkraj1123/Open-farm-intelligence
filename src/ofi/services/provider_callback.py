"""Authenticated, replay-safe provider callback boundary."""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Mapping

from ofi.services.service_transaction import TransactionStatus


class CallbackVerificationError(ValueError):
    """Raised when a provider callback cannot be trusted."""


@dataclass(frozen=True)
class ProviderCallback:
    event_id: str
    transaction_id: str
    provider_id: str
    status: TransactionStatus
    occurred_at: datetime
    signature: str
    external_reference: str | None = None
    message: str = ""

    def signing_payload(self) -> bytes:
        return "|".join(
            (
                self.event_id,
                self.transaction_id,
                self.provider_id,
                self.status,
                self.occurred_at.astimezone(timezone.utc).isoformat(),
                self.external_reference or "",
                self.message,
            )
        ).encode()

    def sign(self, secret: str) -> str:
        return hmac.new(
            secret.encode(),
            self.signing_payload(),
            hashlib.sha256,
        ).hexdigest()


class ProviderCallbackVerifier:
    """Verifies provider identity, HMAC signature, and callback freshness."""

    def __init__(
        self,
        secrets: Mapping[str, str],
        *,
        max_age: timedelta = timedelta(minutes=10),
    ):
        self._secrets = dict(secrets)
        self._max_age = max_age

    def verify(
        self,
        callback: ProviderCallback,
        *,
        as_of: datetime | None = None,
    ) -> None:
        secret = self._secrets.get(callback.provider_id)
        if secret is None:
            raise CallbackVerificationError("unknown callback provider")

        if not hmac.compare_digest(
            callback.sign(secret),
            callback.signature,
        ):
            raise CallbackVerificationError("invalid callback signature")

        now = (as_of or datetime.now(timezone.utc)).astimezone(timezone.utc)
        occurred = callback.occurred_at.astimezone(timezone.utc)
        if abs(now - occurred) > self._max_age:
            raise CallbackVerificationError("stale provider callback")
