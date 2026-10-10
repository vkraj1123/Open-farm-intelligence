"""Guarded creation of a new execution attempt after reconciliation."""

from __future__ import annotations

from ofi.services.service_transaction import ExecutionAttempt
from ofi.services.transaction_repository import TransactionRepository


class GuardedRetryOrchestrator:
    """Create a prepared retry only when the repository proves it is safe.

    This service never calls an external provider. The returned attempt is in
    ready state and must pass through the execution gateway for any later
    submission. The repository is responsible for checking applied,
    provider-bound non-execution evidence and allocating the next attempt
    number atomically.
    """

    def __init__(self, repository: TransactionRepository) -> None:
        self._repository = repository

    def prepare_retry(
        self,
        *,
        transaction_id: str,
        prior_attempt_id: str,
        retry_request_key: str,
        attempt_id: str,
    ) -> ExecutionAttempt:
        """Create or idempotently recover one safely prepared retry attempt."""
        return self._repository.create_retry_attempt(
            transaction_id=transaction_id,
            prior_attempt_id=prior_attempt_id,
            retry_request_key=retry_request_key,
            attempt_id=attempt_id,
        )
