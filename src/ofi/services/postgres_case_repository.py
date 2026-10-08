"""Optional PostgreSQL persistence for case state and audit events."""

from datetime import datetime, timezone
from typing import Any, Callable

from ofi.domain.models import CaseEvent, CaseOutcome, CaseRecord, FarmCase, ReasoningResult
from ofi.services.case_repository import CaseRepository
from ofi.services.db_context import connection_scope


class PostgresCaseRepository(CaseRepository):
    """Durable case repository using an injected psycopg connection factory.

    State and its audit event are written in the same database transaction.
    The repository stores the domain snapshot as JSONB so the domain layer
    remains independent of PostgreSQL schema details.
    """

    def __init__(self, connection_factory: Callable[[], Any]):
        self._connection_factory = connection_factory

    def create(self, case: FarmCase) -> CaseRecord:
        record = CaseRecord(case=case)
        with connection_scope(self._connection_factory) as conn:
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO case_records
                          (id,status,created_at,updated_at,version,case_data,latest_reasoning,outcome)
                        VALUES (%s,%s,%s,%s,0,%s::jsonb,NULL,NULL)
                        """,
                        (
                            case.id,
                            case.status,
                            case.created_at,
                            case.updated_at,
                            self._case_json(case),
                        ),
                    )
        return record

    def get(self, case_id: str) -> CaseRecord:
        with connection_scope(self._connection_factory) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id,status,created_at,updated_at,version,case_data,
                           latest_reasoning,outcome
                    FROM case_records
                    WHERE id=%s
                    """,
                    (case_id,),
                )
                row = cur.fetchone()
                if row is None:
                    raise KeyError(case_id)

                record = self._record_from_row(row)
                cur.execute(
                    """
                    SELECT id,sequence,event_type,occurred_at,actor,payload
                    FROM case_events
                    WHERE case_id=%s
                    ORDER BY sequence
                    """,
                    (case_id,),
                )
                record.case.events = [
                    CaseEvent(
                        id=event_id,
                        sequence=sequence,
                        event_type=event_type,
                        timestamp=occurred_at,
                        actor=actor,
                        payload=payload,
                    )
                    for event_id, sequence, event_type, occurred_at, actor, payload
                    in cur.fetchall()
                ]
                return record

    def save(self, record: CaseRecord) -> CaseRecord:
        working = record.model_copy(deep=True)
        working.case.updated_at = datetime.now(timezone.utc)
        with connection_scope(self._connection_factory) as conn:
            with conn.transaction():
                next_version = self._save_record(conn, working)
        working.version = next_version
        self._apply_committed(record, working)
        return record

    def append_event(self, case_id: str, event: CaseEvent) -> CaseRecord:
        record = self.get(case_id)
        return self.save_and_append_event(record, event)

    def save_and_append_event(
        self, record: CaseRecord, event: CaseEvent
    ) -> CaseRecord:
        if any(existing.id == event.id for existing in record.case.events):
            raise ValueError(f"event already exists: {event.id}")

        working = record.model_copy(deep=True)
        working_event = event.model_copy(deep=True)
        working.case.events.append(working_event)
        working.case.updated_at = datetime.now(timezone.utc)

        try:
            with connection_scope(self._connection_factory) as conn:
                with conn.transaction():
                    next_version = self._save_record(conn, working)
                    with conn.cursor() as cur:
                        # Serialize event-sequence allocation per case. The
                        # case row is the lock/serialization point shared by
                        # all writers, while the optimistic version check
                        # below still detects stale domain snapshots.
                        cur.execute(
                            """
                            SELECT id
                            FROM case_records
                            WHERE id=%s
                            FOR UPDATE
                            """,
                            (working.case.id,),
                        )
                        if cur.fetchone() is None:
                            raise KeyError(working.case.id)
                        cur.execute(
                            """
                            SELECT COALESCE(MAX(sequence), 0) + 1
                            FROM case_events
                            WHERE case_id=%s
                            """,
                            (working.case.id,),
                        )
                        next_sequence = cur.fetchone()[0]
                        if event.sequence is None:
                            working_event.sequence = next_sequence
                        elif event.sequence != next_sequence:
                            raise ValueError(
                                f"invalid event sequence for case {working.case.id}: "
                                f"{event.sequence}; expected {next_sequence}"
                            )
                        # Keep the committed domain snapshot aligned with the
                        # durable ledger sequence assigned in this transaction.
                        working.case.events[-1].sequence = working_event.sequence
                        cur.execute(
                            """
                            INSERT INTO case_events
                              (id,case_id,sequence,event_type,occurred_at,actor,payload)
                            VALUES (%s,%s,%s,%s,%s,%s,%s::jsonb)
                            """,
                            (
                                working_event.id,
                                working.case.id,
                                working_event.sequence,
                                working_event.event_type,
                                working_event.timestamp,
                                working_event.actor,
                                self._json(working_event.payload),
                            ),
                        )
            working.version = next_version
        except Exception:
            raise
        self._apply_committed(record, working)
        return record

    @staticmethod
    def _apply_committed(target: CaseRecord, committed: CaseRecord) -> None:
        target.case = committed.case
        target.version = committed.version
        target.latest_reasoning = committed.latest_reasoning
        target.outcome = committed.outcome

    @staticmethod
    def _case_json(case: FarmCase) -> str:
        data = case.model_dump(mode="json")
        # Events have their own append-only table and are not duplicated in
        # the mutable case snapshot.
        data["events"] = []
        return PostgresCaseRepository._json(data)

    @staticmethod
    def _json(value: Any) -> str:
        import json
        return json.dumps(value)

    def _save_record(self, conn: Any, record: CaseRecord) -> int:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE case_records
                SET status=%s,
                    created_at=%s,
                    updated_at=%s,
                    version=version+1,
                    case_data=%s::jsonb,
                    latest_reasoning=%s::jsonb,
                    outcome=%s::jsonb
                WHERE id=%s AND version=%s
                """,
                (
                    record.case.status,
                    record.case.created_at,
                    record.case.updated_at,
                    self._case_json(record.case),
                    self._json(record.latest_reasoning.model_dump(mode="json"))
                    if record.latest_reasoning else None,
                    self._json(record.outcome.model_dump(mode="json"))
                    if record.outcome else None,
                    record.case.id,
                    record.version,
                ),
            )
            if cur.rowcount != 1:
                raise RuntimeError(f"case version conflict: {record.case.id}")
            return record.version + 1

    @staticmethod
    def _record_from_row(row: tuple[Any, ...]) -> CaseRecord:
        _, _, _, _, version, case_data, reasoning_data, outcome_data = row
        case = FarmCase.model_validate(case_data)
        return CaseRecord(
            case=case,
            version=version,
            latest_reasoning=(
                None if reasoning_data is None
                else ReasoningResult.model_validate(reasoning_data)
            ),
            outcome=(
                None if outcome_data is None
                else CaseOutcome.model_validate(outcome_data)
            ),
        )
