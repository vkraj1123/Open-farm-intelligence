"""Application transaction boundaries for coordinated case + farm-twin mutations."""

from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from typing import Any, Callable, Iterator

from ofi.services.case_repository import CaseRepository, InMemoryCaseRepository
from ofi.twin.farm_twin import FarmTwinStore
from ofi.twin.repository import FarmTwinRepository


class UnitOfWork:
    """Coordinate mutations that must succeed or fail together."""

    case_repository: CaseRepository
    farm_twin: FarmTwinRepository

    @contextmanager
    def atomic(self) -> Iterator[None]:
        yield


class InMemoryFarmCaseUnitOfWork(UnitOfWork):
    """True atomic boundary for the reference in-memory stores."""

    def __init__(
        self,
        case_repository: InMemoryCaseRepository,
        farm_twin: FarmTwinStore,
    ) -> None:
        self.case_repository = case_repository
        self.farm_twin = farm_twin

    @contextmanager
    def atomic(self) -> Iterator[None]:
        original_records = dict(self.case_repository._records)
        case_state = deepcopy(self.case_repository._records)
        twin_state = {
            "_farms": deepcopy(self.farm_twin._farms),
            "_crop_cycles": deepcopy(self.farm_twin._crop_cycles),
            "_parties": deepcopy(self.farm_twin._parties),
            "_contracts": deepcopy(self.farm_twin._contracts),
            "_observations": deepcopy(self.farm_twin._observations),
        }
        try:
            yield
        except Exception:
            for case_id, original in original_records.items():
                restored = case_state[case_id]
                original.case = restored.case
                original.version = restored.version
                original.latest_reasoning = restored.latest_reasoning
                original.outcome = restored.outcome
            self.case_repository._records = original_records
            for name, value in twin_state.items():
                setattr(self.farm_twin, name, value)
            raise


class PostgresFarmCaseUnitOfWork(UnitOfWork):
    """Share one psycopg connection and transaction across both repositories."""

    def __init__(self, connection_factory: Callable[[], Any]) -> None:
        from ofi.services.postgres_case_repository import PostgresCaseRepository
        from ofi.twin.postgis import PostGISFarmTwinStore

        self._connection_factory = connection_factory
        self.case_repository = PostgresCaseRepository(connection_factory)
        self.farm_twin = PostGISFarmTwinStore(connection_factory)

    @contextmanager
    def atomic(self) -> Iterator[None]:
        from ofi.services.db_context import bind_connection, connection_scope

        with connection_scope(self._connection_factory) as conn:
            with conn.transaction():
                with bind_connection(conn):
                    yield
