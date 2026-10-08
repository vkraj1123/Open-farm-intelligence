"""Optional PostgreSQL persistence for case state and audit events."""

from datetime import datetime, timezone
from typing import Any, Callable

from ofi.domain.models import CaseEvent, CaseRecord, FarmCase
from ofi.services.case_repository import CaseRepository


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
        with self._connection_factory() as conn:
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO case_records
                          (id,status,created_at,updated_at,case_data,latest_reasoning,outcome)
                        VALUES (%s,%s,%s,%s,%s::jsonb,NULL,NULL)
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
        with self._connection_factory() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id,status,created_at,updated_at,case_data,
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
                    SELECT id,event_type,occurred_at,actor,payload
                    FROM case_events
                    WHERE case_id=%s
                    ORDER BY occurred_at,id
                    """,
                    (case_id,),
                )
                record.case.events = [
                    CaseEvent(
                        id=event_id,
                        event_type=event_type,
                        timestamp=occurred_at,
                        actor=actor,
                        payload=payload,
                    )
                    for event_id, event_type, occurred_at, actor, payload
                    in cur.fetchall()
                ]
                return record

    def save(self, record: CaseRecord) -> CaseRecord:
        record.case.updated_at = datetime.now(timezone.utc)
        with self._connection_factory() as conn:
            with conn.transaction():
                self._save_record(conn, record)
        return record

    def append_event(self, case_id: str, event: CaseEvent) -> CaseRecord:
        record = self.get(case_id)
        return self.save_and_append_event(record, event)

    def save_and_append_event(
        self, record: CaseRecord, event: CaseEvent
    ) -> CaseRecord:
        if any(existing.id == event.id for existing in record.case.events):
            raise ValueError(f"event already exists: {event.id}")

        record.case.events.append(event)
        record.case.updated_at = datetime.now(timezone.utc)

        try:
            with self._connection_factory() as conn:
                with conn.transaction():
                    self._save_record(conn, record)
                    with conn.cursor() as cur:
                        cur.execute(
                            """
                            INSERT INTO case_events
                              (id,case_id,event_type,occurred_at,actor,payload)
                            VALUES (%s,%s,%s,%s,%s,%s::jsonb)
                            """,
                            (
                                event.id,
                                record.case.id,
                                event.event_type,
                                event.timestamp,
                                event.actor,
                                self._json(event.payload),
                            ),
                        )
        except Exception:
            record.case.events.pop()
            raise
        return record

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

    def _save_record(self, conn: Any, record: CaseRecord) -> None:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE case_records
                SET status=%s,
                    created_at=%s,
                    updated_at=%s,
                    case_data=%s::jsonb,
                    latest_reasoning=%s::jsonb,
                    outcome=%s::jsonb
                WHERE id=%s
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
                ),
            )
            if cur.rowcount != 1:
                raise KeyError(record.case.id)

    @staticmethod
    def _record_from_row(row: tuple[Any, ...]) -> CaseRecord:
        _, _, _, _, case_data, reasoning_data, outcome_data = row
        case = FarmCase.model_validate(case_data)
        return CaseRecord(
            case=case,
            latest_reasoning=(
                None if reasoning_data is None
                else __import__("ofi.domain.models", fromlist=["ReasoningResult"])
                .ReasoningResult.model_validate(reasoning_data)
            ),
            outcome=(
                None if outcome_data is None
                else __import__("ofi.domain.models", fromlist=["CaseOutcome"])
                .CaseOutcome.model_validate(outcome_data)
            ),
        )
