"""Persistence boundary for service execution transactions."""

from __future__ import annotations

from abc import ABC, abstractmethod
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Callable
from threading import RLock

from ofi.services.db_context import connection_scope
from ofi.services.reconciliation import ReconciliationEvidence
from ofi.services.service_transaction import (
    ExecutionAttempt,
    AttemptAuditEvent,
    ServiceTransaction,
    TransactionEvent,
    AttemptStatus,
    TransactionStatus,
)


class TransactionConflictError(RuntimeError):
    """Raised when an idempotency key maps to a different request."""


@dataclass(frozen=True)
class TransactionCreateResult:
    transaction: ServiceTransaction
    created: bool


@dataclass(frozen=True)
class CallbackResult:
    transaction: ServiceTransaction
    applied: bool


class TransactionRepository(ABC):
    """Store for durable service transaction state and append-only events."""

    @abstractmethod
    def create(self, transaction: ServiceTransaction) -> ServiceTransaction:
        """Create or idempotently recover a transaction."""

    @abstractmethod
    def create_if_absent(self, transaction: ServiceTransaction) -> TransactionCreateResult:
        """Create a transaction and report whether this caller won."""

    @abstractmethod
    def update_external_reference(
        self, transaction_id: str, external_reference: str
    ) -> ServiceTransaction:
        """Persist the provider reference without changing lifecycle state."""

    @abstractmethod
    def get(self, transaction_id: str) -> ServiceTransaction:
        """Return a transaction or raise KeyError."""

    @abstractmethod
    def transition(
        self,
        transaction_id: str,
        status: TransactionStatus,
        *,
        external_reference: str | None = None,
        message: str = "",
    ) -> ServiceTransaction:
        """Atomically append a status event and update state."""

    @abstractmethod
    def apply_callback(
        self,
        *,
        provider_id: str,
        event_id: str,
        transaction_id: str,
        attempt_id: str,
        status: TransactionStatus,
        external_reference: str | None = None,
        message: str = "",
    ) -> CallbackResult:
        """Atomically apply a provider callback exactly once."""

    @abstractmethod
    def record_reconciliation_evidence(
        self, evidence: ReconciliationEvidence, *, payload_sha256: str
    ) -> bool:
        """Persist verified evidence once; return False for an identical replay."""

    @abstractmethod
    def apply_reconciliation_evidence(
        self, evidence: ReconciliationEvidence, *, payload_sha256: str
    ) -> bool:
        """Atomically record verified evidence and update its attempt/transaction."""

    @abstractmethod
    def create_retry_attempt(
        self, *, transaction_id: str, prior_attempt_id: str,
        retry_request_key: str, attempt_id: str,
    ) -> ExecutionAttempt:
        """Atomically create or recover a retry after applied non-execution evidence."""

    @abstractmethod
    def create_attempt(self, attempt: ExecutionAttempt) -> ExecutionAttempt:
        """Create a durable execution attempt with monotonic numbering."""

    @abstractmethod
    def claim_attempt_for_dispatch(self, attempt_id: str) -> ExecutionAttempt:
        """Atomically claim a ready attempt before making any provider call."""

    @abstractmethod
    def recover_stale_dispatches(
        self, *, older_than: Any, limit: int = 100
    ) -> list[ExecutionAttempt]:
        """Mark abandoned dispatching attempts unknown; never infer non-execution."""

    @abstractmethod
    def transition_attempt(
        self, attempt_id: str, status: AttemptStatus, *,
        external_reference: str | None = None, last_error: str | None = None,
    ) -> ExecutionAttempt:
        """Atomically update one execution attempt."""

    @abstractmethod
    def list_attempt_events(self, attempt_id: str) -> list[AttemptAuditEvent]:
        """Return append-only audit events in durable event order."""

    @abstractmethod
    def list_attempts(self, transaction_id: str) -> list[ExecutionAttempt]:
        """Return attempts for a transaction in attempt-number order."""

    @abstractmethod
    def list_for_action(self, action_id: str) -> list[ServiceTransaction]:
        """Return transactions for an action in creation order."""


class InMemoryTransactionRepository(TransactionRepository):
    """Deterministic repository used by tests and local development."""

    def __init__(self) -> None:
        self._transactions: dict[str, ServiceTransaction] = {}
        self._idempotency: dict[str, str] = {}
        self._callback_events: dict[tuple[str, str], tuple[str, str]] = {}
        self._reconciliation_events: dict[tuple[str, str], tuple[str, str, str, str, bool]] = {}
        self._attempts: dict[str, ExecutionAttempt] = {}
        self._attempt_events: dict[str, list[AttemptAuditEvent]] = {}
        self._attempt_event_sequence = 0
        self._retry_lock = RLock()


    def _append_attempt_event(
        self,
        attempt: ExecutionAttempt,
        *,
        from_status: AttemptStatus | None,
        event_type: str,
        detail: str | None = None,
    ) -> None:
        self._attempt_event_sequence += 1
        event = AttemptAuditEvent(
            event_id=self._attempt_event_sequence,
            attempt_id=attempt.attempt_id,
            transaction_id=attempt.transaction_id,
            from_status=from_status,
            to_status=attempt.status,
            event_type=event_type,
            occurred_at=__import__("datetime").datetime.now(
                __import__("datetime").timezone.utc
            ),
            external_reference=attempt.external_reference,
            detail=attempt.last_error or "" if detail is None else detail,
        )
        self._attempt_events.setdefault(attempt.attempt_id, []).append(event)

    def create_if_absent(self, transaction: ServiceTransaction) -> TransactionCreateResult:
        existing_id = self._idempotency.get(transaction.idempotency_key)
        if existing_id is not None:
            existing = self._transactions[existing_id]
            if existing.request_fingerprint != transaction.request_fingerprint:
                raise TransactionConflictError(
                    "idempotency key was reused for a different execution request"
                )
            return TransactionCreateResult(deepcopy(existing), False)

        if transaction.transaction_id in self._transactions:
            raise ValueError(f"transaction already exists: {transaction.transaction_id}")

        stored = deepcopy(transaction)
        self._transactions[stored.transaction_id] = stored
        self._idempotency[stored.idempotency_key] = stored.transaction_id
        return TransactionCreateResult(deepcopy(stored), True)

    def create(self, transaction: ServiceTransaction) -> ServiceTransaction:
        return self.create_if_absent(transaction).transaction

    def update_external_reference(
        self, transaction_id: str, external_reference: str
    ) -> ServiceTransaction:
        working = self.get(transaction_id)
        working.external_reference = external_reference
        self._transactions[transaction_id] = deepcopy(working)
        return working

    def get(self, transaction_id: str) -> ServiceTransaction:
        try:
            return deepcopy(self._transactions[transaction_id])
        except KeyError as exc:
            raise KeyError(transaction_id) from exc

    def transition(
        self,
        transaction_id: str,
        status: TransactionStatus,
        *,
        external_reference: str | None = None,
        message: str = "",
    ) -> ServiceTransaction:
        working = self.get(transaction_id)
        working.transition(
            status,
            external_reference=external_reference,
            message=message,
        )
        self._transactions[transaction_id] = deepcopy(working)
        return working

    def apply_callback(
        self,
        *,
        provider_id: str,
        event_id: str,
        transaction_id: str,
        attempt_id: str,
        status: TransactionStatus,
        external_reference: str | None = None,
        message: str = "",
    ) -> CallbackResult:
        # Serialize receipt claim and lifecycle mutation as one in-memory unit.
        with self._retry_lock:
            return self._apply_callback_unlocked(
                provider_id=provider_id,
                event_id=event_id,
                transaction_id=transaction_id,
                attempt_id=attempt_id,
                status=status,
                external_reference=external_reference,
                message=message,
            )

    def _apply_callback_unlocked(
        self,
        *,
        provider_id: str,
        event_id: str,
        transaction_id: str,
        attempt_id: str,
        status: TransactionStatus,
        external_reference: str | None = None,
        message: str = "",
    ) -> CallbackResult:
        event_key = (provider_id, event_id)
        original = self._callback_events.get(event_key)
        if original is not None:
            original_transaction_id, original_attempt_id = original
            if (original_transaction_id, original_attempt_id) != (transaction_id, attempt_id):
                raise TransactionConflictError("provider event is already bound to a different transaction or attempt")
            return CallbackResult(self.get(original_transaction_id), False)
        transaction = self.get(transaction_id)
        if transaction.provider_id != provider_id:
            raise TransactionConflictError(
                "callback provider does not own the transaction"
            )
        try:
            attempt = self._attempts[attempt_id]
        except KeyError as exc:
            raise KeyError(attempt_id) from exc
        if attempt.transaction_id != transaction_id or attempt.provider_id != provider_id:
            raise TransactionConflictError("callback attempt does not belong to this provider transaction")
        prior_attempt_status = attempt.status
        transaction = self.transition(
            transaction_id,
            status,
            external_reference=external_reference,
            message=message,
        )
        updated_attempt = self.transition_attempt(
            attempt_id, status, external_reference=external_reference
        )
        self._append_attempt_event(
            updated_attempt, from_status=prior_attempt_status,
            event_type="provider_callback_applied", detail=message,
        )
        self._callback_events[event_key] = (transaction_id, attempt_id)
        return CallbackResult(transaction, True)

    def record_reconciliation_evidence(
        self, evidence: ReconciliationEvidence, *, payload_sha256: str
    ) -> bool:
        if len(payload_sha256) != 64 or any(ch not in "0123456789abcdef" for ch in payload_sha256):
            raise ValueError("payload_sha256 must be a lowercase SHA-256 hex digest")
        event_key = (evidence.provider_id, evidence.event_id)
        fingerprint = (
            evidence.transaction_id, evidence.attempt_id,
            payload_sha256, evidence.status,
        )
        existing = self._reconciliation_events.get(event_key)
        if existing is not None:
            if existing[:4] != fingerprint:
                raise TransactionConflictError(
                    "reconciliation event is already bound to different evidence"
                )
            return False
        transaction = self.get(evidence.transaction_id)
        if transaction.provider_id != evidence.provider_id:
            raise TransactionConflictError(
                "reconciliation provider does not own the transaction"
            )
        try:
            attempt = self._attempts[evidence.attempt_id]
        except KeyError as exc:
            raise KeyError(evidence.attempt_id) from exc
        if attempt.transaction_id != evidence.transaction_id or attempt.provider_id != evidence.provider_id:
            raise TransactionConflictError(
                "reconciliation attempt does not belong to this provider transaction"
            )
        self._reconciliation_events[event_key] = (*fingerprint, False)
        return True

    def apply_reconciliation_evidence(
        self, evidence: ReconciliationEvidence, *, payload_sha256: str
    ) -> bool:
        # Receipt identity, state update, and audit append must be serialized.
        with self._retry_lock:
            return self._apply_reconciliation_evidence_unlocked(
                evidence, payload_sha256=payload_sha256
            )

    def _apply_reconciliation_evidence_unlocked(
        self, evidence: ReconciliationEvidence, *, payload_sha256: str
    ) -> bool:
        if len(payload_sha256) != 64 or any(ch not in "0123456789abcdef" for ch in payload_sha256):
            raise ValueError("payload_sha256 must be a lowercase SHA-256 hex digest")
        event_key = (evidence.provider_id, evidence.event_id)
        fingerprint = (
            evidence.transaction_id, evidence.attempt_id,
            payload_sha256, evidence.status,
        )
        existing = self._reconciliation_events.get(event_key)
        if existing is not None and existing[:4] != fingerprint:
            raise TransactionConflictError(
                "reconciliation event is already bound to different evidence"
            )
        if existing is not None and existing[4]:
            return False

        transaction = self.get(evidence.transaction_id)
        if transaction.provider_id != evidence.provider_id:
            raise TransactionConflictError(
                "reconciliation provider does not own the transaction"
            )
        try:
            attempt = deepcopy(self._attempts[evidence.attempt_id])
        except KeyError as exc:
            raise KeyError(evidence.attempt_id) from exc
        if attempt.transaction_id != evidence.transaction_id or attempt.provider_id != evidence.provider_id:
            raise TransactionConflictError(
                "reconciliation attempt does not belong to this provider transaction"
            )
        prior_attempt_status = attempt.status

        if evidence.status == "executed":
            if attempt.status in {"rejected", "failed"} or transaction.status in {"rejected", "failed", "cancelled"}:
                raise TransactionConflictError("executed reconciliation contradicts terminal failure state")
            if attempt.status not in {"completed", "in_progress"}:
                attempt.status = "in_progress"
            if transaction.status in {"submitted", "accepted"}:
                transaction.transition(
                    "in_progress",
                    external_reference=evidence.external_reference,
                    message=evidence.message or "Provider reconciliation confirms execution.",
                )
        elif evidence.status == "not_executed":
            if attempt.status in {"accepted", "in_progress", "completed"} or transaction.status in {"accepted", "in_progress", "completed"}:
                raise TransactionConflictError("not-executed reconciliation contradicts active or completed execution")
            attempt.status = "failed"
            attempt.last_error = evidence.message or "Provider reconciliation confirms non-execution."
        elif evidence.status in {"pending", "unknown"}:
            if attempt.status not in {"completed", "rejected", "failed"}:
                attempt.status = "unknown"

        from datetime import datetime, timezone
        attempt.updated_at = datetime.now(timezone.utc)
        if evidence.external_reference is not None:
            attempt.external_reference = evidence.external_reference
        self._attempts[evidence.attempt_id] = deepcopy(attempt)
        self._transactions[evidence.transaction_id] = deepcopy(transaction)
        self._append_attempt_event(
            attempt, from_status=prior_attempt_status,
            event_type="reconciliation_evidence_applied",
            detail=f"{evidence.status}: {evidence.message}",
        )
        self._reconciliation_events[event_key] = (*fingerprint, True)
        return True

    def create_retry_attempt(
        self, *, transaction_id: str, prior_attempt_id: str,
        retry_request_key: str, attempt_id: str,
    ) -> ExecutionAttempt:
        if not retry_request_key.strip() or not attempt_id.strip():
            raise ValueError("retry_request_key and attempt_id must be non-empty")
        with self._retry_lock:
            for existing in self._attempts.values():
                if existing.retry_request_key == retry_request_key:
                    if (existing.transaction_id != transaction_id
                            or existing.retry_of_attempt_id != prior_attempt_id):
                        raise TransactionConflictError(
                            "retry request key is already bound to a different retry"
                        )
                    return deepcopy(existing)

            transaction = self.get(transaction_id)
            if transaction.status != "submitted":
                raise TransactionConflictError(
                    "retry requires a submitted transaction with no active execution"
                )
            prior = self._attempts.get(prior_attempt_id)
            if prior is None:
                raise KeyError(prior_attempt_id)
            if prior.transaction_id != transaction_id or prior.provider_id != transaction.provider_id:
                raise TransactionConflictError("prior attempt does not belong to this transaction")
            if prior.status != "failed":
                raise TransactionConflictError("retry requires a failed prior attempt")
            existing_attempts = self.list_attempts(transaction_id)
            if not existing_attempts or existing_attempts[-1].attempt_id != prior_attempt_id:
                raise TransactionConflictError("retry must follow the latest execution attempt")
            if not any(
                item[0] == transaction_id and item[1] == prior_attempt_id
                and item[3] == "not_executed" and item[4]
                for item in self._reconciliation_events.values()
            ):
                raise TransactionConflictError(
                    "retry requires applied provider evidence confirming non-execution"
                )
            if attempt_id in self._attempts:
                raise TransactionConflictError("attempt ID is already in use")
            next_number = max((item.attempt_number for item in existing_attempts), default=0) + 1
            now = __import__("datetime").datetime.now(__import__("datetime").timezone.utc)
            created = ExecutionAttempt(
                attempt_id=attempt_id, transaction_id=transaction_id,
                attempt_number=next_number, provider_id=prior.provider_id,
                status="ready", created_at=now, updated_at=now,
                retry_request_key=retry_request_key,
                retry_of_attempt_id=prior_attempt_id,
            )
            self._attempts[attempt_id] = deepcopy(created)
            self._append_attempt_event(
                created, from_status=None, event_type="attempt_created"
            )
            return deepcopy(created)

    def create_attempt(self, attempt: ExecutionAttempt) -> ExecutionAttempt:
        with self._retry_lock:
            existing = self.list_attempts(attempt.transaction_id)
            expected = max((item.attempt_number for item in existing), default=0) + 1
            if attempt.attempt_number != expected:
                raise TransactionConflictError(f"expected attempt number {expected}, got {attempt.attempt_number}")
            if attempt.attempt_id in self._attempts:
                raise ValueError(f"attempt already exists: {attempt.attempt_id}")
            self.get(attempt.transaction_id)
            self._attempts[attempt.attempt_id] = deepcopy(attempt)
            self._append_attempt_event(
                attempt, from_status=None, event_type="attempt_created"
            )
            return deepcopy(attempt)

    def claim_attempt_for_dispatch(self, attempt_id: str) -> ExecutionAttempt:
        from datetime import datetime, timezone
        with self._retry_lock:
            try:
                current = deepcopy(self._attempts[attempt_id])
            except KeyError as exc:
                raise KeyError(attempt_id) from exc
            if current.status != "ready":
                raise TransactionConflictError(
                    f"attempt cannot be dispatched from status {current.status!r}"
                )
            transaction = self.get(current.transaction_id)
            if transaction.status != "submitted":
                raise TransactionConflictError(
                    f"attempt cannot be dispatched while transaction is {transaction.status!r}"
                )
            current.status = "dispatching"
            current.updated_at = datetime.now(timezone.utc)
            self._attempts[attempt_id] = deepcopy(current)
            self._append_attempt_event(
                current, from_status="ready", event_type="dispatch_claimed"
            )
            return deepcopy(current)

    def recover_stale_dispatches(
        self, *, older_than: Any, limit: int = 100
    ) -> list[ExecutionAttempt]:
        from datetime import datetime, timezone
        if limit < 1:
            raise ValueError("limit must be positive")
        cutoff = older_than.astimezone(timezone.utc)
        recovered = []
        with self._retry_lock:
            candidates = sorted(
                (item for item in self._attempts.values()
                 if item.updated_at <= cutoff and (
                     item.status == "dispatching"
                     or (item.status == "unknown" and (item.last_error or "").startswith(
                         "dispatch claim exceeded recovery threshold"
                     ))
                 )),
                key=lambda item: (item.updated_at, item.attempt_id),
            )[:limit]
            for item in candidates:
                current = deepcopy(item)
                if current.status == "dispatching":
                    prior_status = current.status
                    current.status = "unknown"
                    current.updated_at = datetime.now(timezone.utc)
                    current.last_error = (
                        "dispatch claim exceeded recovery threshold; provider outcome unknown"
                    )
                    self._attempts[current.attempt_id] = deepcopy(current)
                    self._append_attempt_event(
                        current, from_status=prior_status,
                        event_type="stale_dispatch_recovered",
                    )
                # Unknown attempts with this marker are returned again after
                # the next stale interval if provider reconciliation failed or
                # remained ambiguous; never reset them to dispatching.
                recovered.append(deepcopy(current))
        return recovered

    def transition_attempt(self, attempt_id: str, status: AttemptStatus, *,
                           external_reference: str | None = None,
                           last_error: str | None = None) -> ExecutionAttempt:
        from datetime import datetime, timezone
        with self._retry_lock:
            try:
                current = deepcopy(self._attempts[attempt_id])
            except KeyError as exc:
                raise KeyError(attempt_id) from exc
            prior_status = current.status
            current.status = status
            current.updated_at = datetime.now(timezone.utc)
            if external_reference is not None:
                current.external_reference = external_reference
            if last_error is not None:
                current.last_error = last_error
            self._attempts[attempt_id] = deepcopy(current)
            if prior_status != current.status:
                event_type = (
                    "execution_outcome_unknown" if status == "unknown"
                    else "provider_submission_acknowledged" if status == "submitted"
                    else "status_changed"
                )
                self._append_attempt_event(
                    current, from_status=prior_status, event_type=event_type
                )
            return deepcopy(current)

    def list_attempt_events(self, attempt_id: str) -> list[AttemptAuditEvent]:
        if attempt_id not in self._attempts:
            raise KeyError(attempt_id)
        return list(self._attempt_events.get(attempt_id, []))

    def list_attempts(self, transaction_id: str) -> list[ExecutionAttempt]:
        return sorted(
            (deepcopy(a) for a in self._attempts.values() if a.transaction_id == transaction_id),
            key=lambda a: a.attempt_number,
        )

    def list_for_action(self, action_id: str) -> list[ServiceTransaction]:
        matches = [
            deepcopy(tx)
            for tx in self._transactions.values()
            if tx.action_id == action_id
        ]
        return sorted(matches, key=lambda tx: (tx.created_at, tx.transaction_id))


class PostgresTransactionRepository(TransactionRepository):
    """PostgreSQL implementation with durable idempotency and event history."""

    def __init__(self, connection_factory: Callable[[], Any]):
        self._connection_factory = connection_factory

    def create_if_absent(self, transaction: ServiceTransaction) -> TransactionCreateResult:
        with connection_scope(self._connection_factory) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO service_transactions (
                        transaction_id, idempotency_key, request_fingerprint,
                        action_id, provider_id, created_at, status, external_reference
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (idempotency_key) DO NOTHING
                    RETURNING transaction_id
                    """,
                    (
                        transaction.transaction_id,
                        transaction.idempotency_key,
                        transaction.request_fingerprint,
                        transaction.action_id,
                        transaction.provider_id,
                        transaction.created_at,
                        transaction.status,
                        transaction.external_reference,
                    ),
                )
                inserted = cur.fetchone()

                if inserted is not None:
                    for sequence, event in enumerate(transaction.events, start=1):
                        cur.execute(
                            """
                            INSERT INTO service_transaction_events (
                                transaction_id, sequence, status, occurred_at,
                                external_reference, message
                            )
                            VALUES (%s, %s, %s, %s, %s, %s)
                            """,
                            (
                                event.transaction_id,
                                sequence,
                                event.status,
                                event.occurred_at,
                                event.external_reference,
                                event.message,
                            ),
                        )
                    return TransactionCreateResult(
                        self._get_with_cursor(cur, transaction.transaction_id),
                        True,
                    )

                cur.execute(
                    """
                    SELECT transaction_id, request_fingerprint
                    FROM service_transactions
                    WHERE idempotency_key = %s
                    FOR UPDATE
                    """,
                    (transaction.idempotency_key,),
                )
                row = cur.fetchone()
                if row is None:
                    raise RuntimeError("idempotency conflict row disappeared")
                if row[1] != transaction.request_fingerprint:
                    raise TransactionConflictError(
                        "idempotency key was reused for a different execution request"
                    )
                return TransactionCreateResult(
                    self._get_with_cursor(cur, row[0]),
                    False,
                )

    def create(self, transaction: ServiceTransaction) -> ServiceTransaction:
        return self.create_if_absent(transaction).transaction

    def update_external_reference(
        self, transaction_id: str, external_reference: str
    ) -> ServiceTransaction:
        with connection_scope(self._connection_factory) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE service_transactions
                    SET external_reference = %s
                    WHERE transaction_id = %s
                    """,
                    (external_reference, transaction_id),
                )
                if cur.rowcount != 1:
                    raise KeyError(transaction_id)
                return self._get_with_cursor(cur, transaction_id)

    def get(self, transaction_id: str) -> ServiceTransaction:
        with connection_scope(self._connection_factory) as conn:
            with conn.cursor() as cur:
                return self._get_with_cursor(cur, transaction_id)

    def transition(
        self,
        transaction_id: str,
        status: TransactionStatus,
        *,
        external_reference: str | None = None,
        message: str = "",
    ) -> ServiceTransaction:
        with connection_scope(self._connection_factory) as conn:
            with conn.cursor() as cur:
                working = self._get_with_cursor(cur, transaction_id, for_update=True)
                working.transition(
                    status,
                    external_reference=external_reference,
                    message=message,
                )
                event = working.events[-1]
                sequence = len(working.events)

                cur.execute(
                    """
                    UPDATE service_transactions
                    SET status = %s, external_reference = %s
                    WHERE transaction_id = %s
                    """,
                    (
                        working.status,
                        working.external_reference,
                        working.transaction_id,
                    ),
                )
                cur.execute(
                    """
                    INSERT INTO service_transaction_events (
                        transaction_id, sequence, status, occurred_at,
                        external_reference, message
                    )
                    VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                    (
                        event.transaction_id,
                        sequence,
                        event.status,
                        event.occurred_at,
                        event.external_reference,
                        event.message,
                    ),
                )
                return working

    def apply_callback(
        self,
        *,
        provider_id: str,
        event_id: str,
        transaction_id: str,
        attempt_id: str,
        status: TransactionStatus,
        external_reference: str | None = None,
        message: str = "",
    ) -> CallbackResult:
        with connection_scope(self._connection_factory) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO service_transaction_callbacks (
                        provider_id, event_id, transaction_id, attempt_id
                    )
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT (provider_id, event_id) DO NOTHING
                    RETURNING event_id
                    """,
                    (provider_id, event_id, transaction_id, attempt_id),
                )
                inserted = cur.fetchone()
                if inserted is None:
                    cur.execute(
                        """
                        SELECT transaction_id, attempt_id
                        FROM service_transaction_callbacks
                        WHERE provider_id = %s AND event_id = %s
                        """,
                        (provider_id, event_id),
                    )
                    row = cur.fetchone()
                    if row is None:
                        raise RuntimeError("callback receipt disappeared")
                    original_transaction_id, original_attempt_id = row
                    if (original_transaction_id, original_attempt_id) != (transaction_id, attempt_id):
                        raise TransactionConflictError("provider event is already bound to a different transaction or attempt")
                    return CallbackResult(
                        self._get_with_cursor(cur, original_transaction_id), False
                    )

                working = self._get_with_cursor(cur, transaction_id, for_update=True)
                if working.provider_id != provider_id:
                    raise TransactionConflictError(
                        "callback provider does not own the transaction"
                    )
                attempt = self._get_attempt_with_cursor(cur, attempt_id)
                if attempt.transaction_id != transaction_id or attempt.provider_id != provider_id:
                    raise TransactionConflictError("callback attempt does not belong to this provider transaction")
                working.transition(
                    status,
                    external_reference=external_reference,
                    message=message,
                )
                event = working.events[-1]
                sequence = len(working.events)
                cur.execute(
                    """
                    UPDATE service_transactions
                    SET status = %s, external_reference = %s
                    WHERE transaction_id = %s
                    """,
                    (working.status, working.external_reference, working.transaction_id),
                )
                cur.execute(
                    """
                    INSERT INTO service_transaction_events (
                        transaction_id, sequence, status, occurred_at,
                        external_reference, message
                    )
                    VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                    (
                        event.transaction_id, sequence, event.status,
                        event.occurred_at, event.external_reference, event.message,
                    ),
                )
                cur.execute(
                    """
                    UPDATE service_execution_attempts
                    SET status = %s, updated_at = now(),
                        external_reference = COALESCE(%s, external_reference)
                    WHERE attempt_id = %s AND transaction_id = %s AND provider_id = %s
                    """,
                    (status, external_reference, attempt_id, transaction_id, provider_id),
                )
                if cur.rowcount != 1:
                    raise TransactionConflictError("callback attempt changed during update")
                cur.execute(
                    """
                    INSERT INTO service_execution_attempt_events (
                        transaction_id, attempt_id, from_status, to_status,
                        event_type, occurred_at, external_reference, detail
                    )
                    VALUES (%s, %s, %s, %s, 'provider_callback_applied',
                            now(), %s, %s)
                    """,
                    (
                        transaction_id, attempt_id, attempt.status, status,
                        external_reference, message,
                    ),
                )
                return CallbackResult(working, True)

    def record_reconciliation_evidence(
        self, evidence: ReconciliationEvidence, *, payload_sha256: str
    ) -> bool:
        if len(payload_sha256) != 64 or any(ch not in "0123456789abcdef" for ch in payload_sha256):
            raise ValueError("payload_sha256 must be a lowercase SHA-256 hex digest")
        with connection_scope(self._connection_factory) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT t.provider_id, a.transaction_id, a.provider_id
                    FROM service_transactions AS t
                    JOIN service_execution_attempts AS a
                      ON a.attempt_id = %s
                    WHERE t.transaction_id = %s
                    FOR UPDATE OF t, a
                    """,
                    (evidence.attempt_id, evidence.transaction_id),
                )
                owner = cur.fetchone()
                if owner is None:
                    raise KeyError(evidence.attempt_id)
                if owner[0] != evidence.provider_id or owner[1] != evidence.transaction_id or owner[2] != evidence.provider_id:
                    raise TransactionConflictError(
                        "reconciliation attempt does not belong to this provider transaction"
                    )
                cur.execute(
                    """
                    INSERT INTO service_reconciliation_events (
                        provider_id, event_id, transaction_id, attempt_id,
                        status, checked_at, payload_sha256, external_reference, message, applied
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, FALSE)
                    ON CONFLICT (provider_id, event_id) DO NOTHING
                    RETURNING event_id
                    """,
                    (
                        evidence.provider_id, evidence.event_id,
                        evidence.transaction_id, evidence.attempt_id,
                        evidence.status, evidence.checked_at, payload_sha256,
                        evidence.external_reference, evidence.message,
                    ),
                )
                inserted = cur.fetchone()
                if inserted is not None:
                    return True
                cur.execute(
                    """
                    SELECT transaction_id, attempt_id, payload_sha256, status, applied
                    FROM service_reconciliation_events
                    WHERE provider_id = %s AND event_id = %s
                    """,
                    (evidence.provider_id, evidence.event_id),
                )
                existing = cur.fetchone()
                if existing is None:
                    raise RuntimeError("reconciliation event receipt disappeared")
                fingerprint = (
                    evidence.transaction_id, evidence.attempt_id,
                    payload_sha256, evidence.status,
                )
                if tuple(existing[:4]) != fingerprint:
                    raise TransactionConflictError(
                        "reconciliation event is already bound to different evidence"
                    )
                return False

    def apply_reconciliation_evidence(
        self, evidence: ReconciliationEvidence, *, payload_sha256: str
    ) -> bool:
        if len(payload_sha256) != 64 or any(ch not in "0123456789abcdef" for ch in payload_sha256):
            raise ValueError("payload_sha256 must be a lowercase SHA-256 hex digest")
        with connection_scope(self._connection_factory) as conn:
            with conn.cursor() as cur:
                transaction = self._get_with_cursor(cur, evidence.transaction_id, for_update=True)
                attempt = self._get_attempt_with_cursor(cur, evidence.attempt_id)
                if transaction.provider_id != evidence.provider_id or attempt.provider_id != evidence.provider_id or attempt.transaction_id != evidence.transaction_id:
                    raise TransactionConflictError(
                        "reconciliation attempt does not belong to this provider transaction"
                    )
                cur.execute(
                    """
                    INSERT INTO service_reconciliation_events (
                        provider_id, event_id, transaction_id, attempt_id,
                        status, checked_at, payload_sha256, external_reference, message
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (provider_id, event_id) DO NOTHING
                    RETURNING event_id
                    """,
                    (
                        evidence.provider_id, evidence.event_id,
                        evidence.transaction_id, evidence.attempt_id,
                        evidence.status, evidence.checked_at, payload_sha256,
                        evidence.external_reference, evidence.message,
                    ),
                )
                if cur.fetchone() is None:
                    cur.execute(
                        """
                        SELECT transaction_id, attempt_id, payload_sha256, status, applied
                        FROM service_reconciliation_events
                        WHERE provider_id = %s AND event_id = %s
                        """,
                        (evidence.provider_id, evidence.event_id),
                    )
                    row = cur.fetchone()
                    fingerprint = (
                        evidence.transaction_id, evidence.attempt_id,
                        payload_sha256, evidence.status,
                    )
                    if row is None:
                        raise RuntimeError("reconciliation event receipt disappeared")
                    if tuple(row[:4]) != fingerprint:
                        raise TransactionConflictError(
                            "reconciliation event is already bound to different evidence"
                        )
                    if row[4]:
                        return False

                prior_attempt_status = attempt.status
                original_transaction_status = transaction.status
                if evidence.status == "executed":
                    if attempt.status in {"rejected", "failed"} or transaction.status in {"rejected", "failed", "cancelled"}:
                        raise TransactionConflictError("executed reconciliation contradicts terminal failure state")
                    if attempt.status not in {"completed", "in_progress"}:
                        attempt.status = "in_progress"
                    if transaction.status in {"submitted", "accepted"}:
                        transaction.transition(
                            "in_progress",
                            external_reference=evidence.external_reference,
                            message=evidence.message or "Provider reconciliation confirms execution.",
                        )
                elif evidence.status == "not_executed":
                    if attempt.status in {"accepted", "in_progress", "completed"} or transaction.status in {"accepted", "in_progress", "completed"}:
                        raise TransactionConflictError("not-executed reconciliation contradicts active or completed execution")
                    attempt.status = "failed"
                    attempt.last_error = evidence.message or "Provider reconciliation confirms non-execution."
                elif evidence.status in {"pending", "unknown"}:
                    if attempt.status not in {"completed", "rejected", "failed"}:
                        attempt.status = "unknown"

                from datetime import datetime, timezone
                attempt.updated_at = datetime.now(timezone.utc)
                if evidence.external_reference is not None:
                    attempt.external_reference = evidence.external_reference
                cur.execute(
                    """
                    UPDATE service_execution_attempts
                    SET status=%s, updated_at=%s,
                        external_reference=COALESCE(%s, external_reference),
                        last_error=COALESCE(%s, last_error)
                    WHERE attempt_id=%s
                    """,
                    (
                        attempt.status, attempt.updated_at, attempt.external_reference,
                        attempt.last_error, attempt.attempt_id,
                    ),
                )
                cur.execute(
                    """
                    INSERT INTO service_execution_attempt_events (
                        transaction_id, attempt_id, from_status, to_status,
                        event_type, occurred_at, external_reference, detail
                    )
                    VALUES (%s, %s, %s, %s, 'reconciliation_evidence_applied',
                            now(), %s, %s)
                    """,
                    (
                        evidence.transaction_id, evidence.attempt_id,
                        prior_attempt_status, attempt.status,
                        evidence.external_reference,
                        f"{evidence.status}: {evidence.message}",
                    ),
                )
                cur.execute(
                    """
                    UPDATE service_reconciliation_events
                    SET applied = TRUE
                    WHERE provider_id = %s AND event_id = %s
                    """,
                    (evidence.provider_id, evidence.event_id),
                )
                if transaction.status != original_transaction_status:
                    latest_event = transaction.events[-1]
                    cur.execute(
                        """
                        UPDATE service_transactions
                        SET status=%s, external_reference=%s
                        WHERE transaction_id=%s
                        """,
                        (transaction.status, transaction.external_reference, transaction.transaction_id),
                    )
                    cur.execute(
                        """
                        INSERT INTO service_transaction_events (
                            transaction_id, sequence, status, occurred_at,
                            external_reference, message
                        )
                        VALUES (%s, %s, %s, %s, %s, %s)
                        """,
                        (
                            latest_event.transaction_id, len(transaction.events),
                            latest_event.status, latest_event.occurred_at,
                            latest_event.external_reference, latest_event.message,
                        ),
                    )
                return True

    def create_retry_attempt(
        self, *, transaction_id: str, prior_attempt_id: str,
        retry_request_key: str, attempt_id: str,
    ) -> ExecutionAttempt:
        if not retry_request_key.strip() or not attempt_id.strip():
            raise ValueError("retry_request_key and attempt_id must be non-empty")
        with connection_scope(self._connection_factory) as conn:
            with conn.cursor() as cur:
                transaction = self._get_with_cursor(cur, transaction_id, for_update=True)
                cur.execute(
                    "SELECT attempt_id, transaction_id, attempt_number, provider_id, status, "
                    "created_at, updated_at, external_reference, last_error, retry_request_key, retry_of_attempt_id "
                    "FROM service_execution_attempts WHERE retry_request_key = %s FOR UPDATE",
                    (retry_request_key,),
                )
                replay = cur.fetchone()
                if replay is not None:
                    existing = self._attempt_from_row(replay)
                    if (existing.transaction_id != transaction_id
                            or existing.retry_of_attempt_id != prior_attempt_id):
                        raise TransactionConflictError(
                            "retry request key is already bound to a different retry"
                        )
                    return existing

                if transaction.status != "submitted":
                    raise TransactionConflictError(
                        "retry requires a submitted transaction with no active execution"
                    )
                prior = self._get_attempt_with_cursor(cur, prior_attempt_id)
                if prior.transaction_id != transaction_id or prior.provider_id != transaction.provider_id:
                    raise TransactionConflictError("prior attempt does not belong to this transaction")
                if prior.status != "failed":
                    raise TransactionConflictError("retry requires a failed prior attempt")
                cur.execute(
                    "SELECT attempt_id FROM service_execution_attempts "
                    "WHERE transaction_id = %s ORDER BY attempt_number DESC LIMIT 1",
                    (transaction_id,),
                )
                latest = cur.fetchone()
                if latest is None or latest[0] != prior_attempt_id:
                    raise TransactionConflictError("retry must follow the latest execution attempt")
                cur.execute(
                    "SELECT 1 FROM service_reconciliation_events "
                    "WHERE provider_id = %s AND transaction_id = %s AND attempt_id = %s "
                    "AND status = 'not_executed' AND applied = TRUE LIMIT 1",
                    (transaction.provider_id, transaction_id, prior_attempt_id),
                )
                if cur.fetchone() is None:
                    raise TransactionConflictError(
                        "retry requires applied provider evidence confirming non-execution"
                    )
                cur.execute(
                    "SELECT COALESCE(MAX(attempt_number), 0) + 1 "
                    "FROM service_execution_attempts WHERE transaction_id = %s",
                    (transaction_id,),
                )
                next_number = cur.fetchone()[0]
                from datetime import datetime, timezone
                now = datetime.now(timezone.utc)
                cur.execute(
                    "INSERT INTO service_execution_attempts "
                    "(attempt_id, transaction_id, attempt_number, provider_id, status, created_at, updated_at, "
                    "retry_request_key, retry_of_attempt_id) "
                    "VALUES (%s,%s,%s,%s,'ready',%s,%s,%s,%s) RETURNING attempt_id",
                    (attempt_id, transaction_id, next_number, prior.provider_id, now, now,
                     retry_request_key, prior_attempt_id),
                )
                cur.fetchone()
                return self._get_attempt_with_cursor(cur, attempt_id)

    def create_attempt(self, attempt: ExecutionAttempt) -> ExecutionAttempt:
        with connection_scope(self._connection_factory) as conn:
            with conn.cursor() as cur:
                self._get_with_cursor(cur, attempt.transaction_id, for_update=True)
                cur.execute(
                    "SELECT COALESCE(MAX(attempt_number), 0) + 1 "
                    "FROM service_execution_attempts WHERE transaction_id = %s",
                    (attempt.transaction_id,),
                )
                expected = cur.fetchone()[0]
                if attempt.attempt_number != expected:
                    raise TransactionConflictError(
                        f"expected attempt number {expected}, got {attempt.attempt_number}"
                    )
                cur.execute(
                    "INSERT INTO service_execution_attempts "
                    "(attempt_id, transaction_id, attempt_number, provider_id, status, created_at, updated_at, external_reference, last_error, retry_request_key, retry_of_attempt_id) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING attempt_id",
                    (attempt.attempt_id, attempt.transaction_id, attempt.attempt_number,
                     attempt.provider_id, attempt.status, attempt.created_at, attempt.updated_at,
                     attempt.external_reference, attempt.last_error, attempt.retry_request_key,
                     attempt.retry_of_attempt_id),
                )
                cur.fetchone()
                return self._get_attempt_with_cursor(cur, attempt.attempt_id)

    def claim_attempt_for_dispatch(self, attempt_id: str) -> ExecutionAttempt:
        with connection_scope(self._connection_factory) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT transaction_id FROM service_execution_attempts WHERE attempt_id=%s",
                    (attempt_id,),
                )
                identity = cur.fetchone()
                if identity is None:
                    raise KeyError(attempt_id)
                transaction = self._get_with_cursor(cur, identity[0], for_update=True)
                cur.execute(
                    "SELECT status FROM service_execution_attempts WHERE attempt_id=%s FOR UPDATE",
                    (attempt_id,),
                )
                row = cur.fetchone()
                if row is None:
                    raise KeyError(attempt_id)
                if row[0] != "ready":
                    raise TransactionConflictError(
                        f"attempt cannot be dispatched from status {row[0]!r}"
                    )
                if transaction.status != "submitted":
                    raise TransactionConflictError(
                        f"attempt cannot be dispatched while transaction is {transaction.status!r}"
                    )
                cur.execute(
                    "UPDATE service_execution_attempts SET status='dispatching', updated_at=now() "
                    "WHERE attempt_id=%s AND status='ready'",
                    (attempt_id,),
                )
                if cur.rowcount != 1:
                    raise TransactionConflictError("attempt was concurrently claimed for dispatch")
                return self._get_attempt_with_cursor(cur, attempt_id)

    def recover_stale_dispatches(
        self, *, older_than: Any, limit: int = 100
    ) -> list[ExecutionAttempt]:
        if limit < 1:
            raise ValueError("limit must be positive")
        with connection_scope(self._connection_factory) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT attempt_id, status
                    FROM service_execution_attempts
                    WHERE updated_at <= %s
                      AND (
                        status = 'dispatching'
                        OR (status = 'unknown' AND starts_with(
                            last_error, 'dispatch claim exceeded recovery threshold'
                        ))
                      )
                    ORDER BY updated_at, attempt_id
                    LIMIT %s
                    FOR UPDATE SKIP LOCKED
                    """,
                    (older_than, limit),
                )
                candidates = cur.fetchall()
                recovered = []
                for attempt_id, status in candidates:
                    if status == "dispatching":
                        cur.execute(
                            "UPDATE service_execution_attempts "
                            "SET status='unknown', updated_at=now(), "
                            "last_error='dispatch claim exceeded recovery threshold; provider outcome unknown' "
                            "WHERE attempt_id=%s AND status='dispatching'",
                            (attempt_id,),
                        )
                    recovered.append(self._get_attempt_with_cursor(cur, attempt_id))
                return recovered

    def transition_attempt(self, attempt_id: str, status: AttemptStatus, *,
                           external_reference: str | None = None,
                           last_error: str | None = None) -> ExecutionAttempt:
        with connection_scope(self._connection_factory) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE service_execution_attempts SET status=%s, updated_at=now(), "
                    "external_reference=COALESCE(%s,external_reference), "
                    "last_error=COALESCE(%s,last_error) WHERE attempt_id=%s",
                    (status, external_reference, last_error, attempt_id),
                )
                if cur.rowcount != 1:
                    raise KeyError(attempt_id)
                return self._get_attempt_with_cursor(cur, attempt_id)

    def list_attempt_events(self, attempt_id: str) -> list[AttemptAuditEvent]:
        with connection_scope(self._connection_factory) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT event_id, attempt_id, transaction_id, from_status,
                           to_status, event_type, occurred_at, external_reference, detail
                    FROM service_execution_attempt_events
                    WHERE attempt_id = %s
                    ORDER BY event_id
                    """,
                    (attempt_id,),
                )
                rows = cur.fetchall()
                if not rows:
                    cur.execute(
                        "SELECT 1 FROM service_execution_attempts WHERE attempt_id = %s",
                        (attempt_id,),
                    )
                    if cur.fetchone() is None:
                        raise KeyError(attempt_id)
                return [
                    AttemptAuditEvent(
                        event_id=row[0], attempt_id=row[1], transaction_id=row[2],
                        from_status=row[3], to_status=row[4], event_type=row[5],
                        occurred_at=row[6], external_reference=row[7], detail=row[8],
                    )
                    for row in rows
                ]

    def list_attempts(self, transaction_id: str) -> list[ExecutionAttempt]:
        with connection_scope(self._connection_factory) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT attempt_id FROM service_execution_attempts WHERE transaction_id=%s ORDER BY attempt_number",
                            (transaction_id,))
                return [self._get_attempt_with_cursor(cur, row[0]) for row in cur.fetchall()]

    def list_for_action(self, action_id: str) -> list[ServiceTransaction]:
        with connection_scope(self._connection_factory) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT transaction_id
                    FROM service_transactions
                    WHERE action_id = %s
                    ORDER BY created_at, transaction_id
                    """,
                    (action_id,),
                )
                ids = [row[0] for row in cur.fetchall()]
                return [self._get_with_cursor(cur, tx_id) for tx_id in ids]

    @staticmethod
    def _get_attempt_with_cursor(cur: Any, attempt_id: str) -> ExecutionAttempt:
        cur.execute(
            "SELECT attempt_id, transaction_id, attempt_number, provider_id, status, "
            "created_at, updated_at, external_reference, last_error, retry_request_key, retry_of_attempt_id "
            "FROM service_execution_attempts WHERE attempt_id=%s",
            (attempt_id,),
        )
        row = cur.fetchone()
        if row is None:
            raise KeyError(attempt_id)
        return PostgresTransactionRepository._attempt_from_row(row)

    @staticmethod
    def _attempt_from_row(row: Any) -> ExecutionAttempt:
        return ExecutionAttempt(
            attempt_id=row[0], transaction_id=row[1], attempt_number=row[2],
            provider_id=row[3], status=row[4], created_at=row[5],
            updated_at=row[6], external_reference=row[7], last_error=row[8],
            retry_request_key=row[9], retry_of_attempt_id=row[10],
        )

    @staticmethod
    def _get_with_cursor(
        cur: Any,
        transaction_id: str,
        *,
        for_update: bool = False,
    ) -> ServiceTransaction:
        suffix = " FOR UPDATE" if for_update else ""
        cur.execute(
            f"""
            SELECT transaction_id, idempotency_key, request_fingerprint,
                   action_id, provider_id, created_at, status, external_reference
            FROM service_transactions
            WHERE transaction_id = %s
            {suffix}
            """,
            (transaction_id,),
        )
        row = cur.fetchone()
        if row is None:
            raise KeyError(transaction_id)

        transaction = ServiceTransaction(
            transaction_id=row[0],
            idempotency_key=row[1],
            request_fingerprint=row[2],
            action_id=row[3],
            provider_id=row[4],
            created_at=row[5],
            status=row[6],
            external_reference=row[7],
        )
        cur.execute(
            """
            SELECT transaction_id, status, occurred_at,
                   external_reference, message
            FROM service_transaction_events
            WHERE transaction_id = %s
            ORDER BY sequence
            """,
            (transaction_id,),
        )
        transaction.events = [
            TransactionEvent(
                transaction_id=event[0],
                status=event[1],
                occurred_at=event[2],
                external_reference=event[3],
                message=event[4],
            )
            for event in cur.fetchall()
        ]
        return transaction
