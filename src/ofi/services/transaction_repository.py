"""Persistence boundary for service execution transactions."""

from __future__ import annotations

from abc import ABC, abstractmethod
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Callable

from ofi.services.db_context import connection_scope
from ofi.services.service_transaction import (
    ServiceTransaction,
    TransactionEvent,
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
        status: TransactionStatus,
        external_reference: str | None = None,
        message: str = "",
    ) -> CallbackResult:
        """Atomically apply a provider callback exactly once."""

    @abstractmethod
    def list_for_action(self, action_id: str) -> list[ServiceTransaction]:
        """Return transactions for an action in creation order."""


class InMemoryTransactionRepository(TransactionRepository):
    """Deterministic repository used by tests and local development."""

    def __init__(self) -> None:
        self._transactions: dict[str, ServiceTransaction] = {}
        self._idempotency: dict[str, str] = {}
        self._callback_events: set[tuple[str, str]] = set()

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
        status: TransactionStatus,
        external_reference: str | None = None,
        message: str = "",
    ) -> CallbackResult:
        event_key = (provider_id, event_id)
        if event_key in self._callback_events:
            return CallbackResult(self.get(transaction_id), False)
        transaction = self.get(transaction_id)
        if transaction.provider_id != provider_id:
            raise TransactionConflictError(
                "callback provider does not own the transaction"
            )
        transaction = self.transition(
            transaction_id,
            status,
            external_reference=external_reference,
            message=message,
        )
        self._callback_events.add(event_key)
        return CallbackResult(transaction, True)

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
        status: TransactionStatus,
        external_reference: str | None = None,
        message: str = "",
    ) -> CallbackResult:
        with connection_scope(self._connection_factory) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO service_transaction_callbacks (
                        provider_id, event_id, transaction_id
                    )
                    VALUES (%s, %s, %s)
                    ON CONFLICT (provider_id, event_id) DO NOTHING
                    RETURNING event_id
                    """,
                    (provider_id, event_id, transaction_id),
                )
                inserted = cur.fetchone()
                if inserted is None:
                    cur.execute(
                        """
                        SELECT transaction_id
                        FROM service_transaction_callbacks
                        WHERE provider_id = %s AND event_id = %s
                        """,
                        (provider_id, event_id),
                    )
                    row = cur.fetchone()
                    if row is None:
                        raise RuntimeError("callback receipt disappeared")
                    original_transaction_id = row[0]
                    return CallbackResult(
                        self._get_with_cursor(cur, original_transaction_id), False
                    )

                working = self._get_with_cursor(cur, transaction_id, for_update=True)
                if working.provider_id != provider_id:
                    raise TransactionConflictError(
                        "callback provider does not own the transaction"
                    )
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
                return CallbackResult(working, True)

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
