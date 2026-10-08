from abc import ABC, abstractmethod

from ofi.domain.models import CaseEvent, CaseRecord, FarmCase


class CaseRepository(ABC):
    """Persistence boundary for case state and its audit events."""

    @abstractmethod
    def create(self, case: FarmCase) -> CaseRecord:
        raise NotImplementedError

    @abstractmethod
    def get(self, case_id: str) -> CaseRecord:
        raise NotImplementedError

    @abstractmethod
    def save(self, record: CaseRecord) -> CaseRecord:
        raise NotImplementedError

    @abstractmethod
    def append_event(self, case_id: str, event: CaseEvent) -> CaseRecord:
        raise NotImplementedError

    @abstractmethod
    def save_and_append_event(
        self, record: CaseRecord, event: CaseEvent
    ) -> CaseRecord:
        """Atomically persist current state and its corresponding event."""
        raise NotImplementedError


class InMemoryCaseRepository(CaseRepository):
    """Reference implementation for tests and local development."""

    def __init__(self):
        self._records: dict[str, CaseRecord] = {}

    def create(self, case: FarmCase) -> CaseRecord:
        if case.id in self._records:
            raise ValueError(f"case already exists: {case.id}")
        record = CaseRecord(case=case)
        self._records[case.id] = record
        return record

    def get(self, case_id: str) -> CaseRecord:
        try:
            return self._records[case_id]
        except KeyError as exc:
            raise KeyError(case_id) from exc

    def save(self, record: CaseRecord) -> CaseRecord:
        record.version += 1
        self._records[record.case.id] = record
        return record

    def append_event(self, case_id: str, event: CaseEvent) -> CaseRecord:
        record = self.get(case_id)
        return self.save_and_append_event(record, event)

    def save_and_append_event(self, record: CaseRecord, event: CaseEvent) -> CaseRecord:
        if any(existing.id == event.id for existing in record.case.events):
            raise ValueError(f"event already exists: {event.id}")
        if event.sequence is None:
            event.sequence = max(
                (existing.sequence or 0 for existing in record.case.events),
                default=0,
            ) + 1
        elif event.sequence != len(record.case.events) + 1:
            raise ValueError(
                f"invalid event sequence for case {record.case.id}: {event.sequence}"
            )
        record.case.events.append(event)
        return self.save(record)
