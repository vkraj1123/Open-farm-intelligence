from contextlib import contextmanager

from ofi.services.db_context import connection_scope
from ofi.services.unit_of_work import PostgresFarmCaseUnitOfWork


class FakeConnection:
    def __init__(self):
        self.transaction_entered = 0
        self.transaction_exited = 0

    @contextmanager
    def transaction(self):
        self.transaction_entered += 1
        try:
            yield self
        finally:
            self.transaction_exited += 1

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def test_postgres_unit_of_work_binds_one_connection_across_repositories():
    connection = FakeConnection()
    calls = 0

    def factory():
        nonlocal calls
        calls += 1
        return connection

    uow = PostgresFarmCaseUnitOfWork(factory)

    with uow.atomic():
        with connection_scope(factory) as active:
            assert active is connection
        with connection_scope(factory) as active:
            assert active is connection

    assert calls == 1
    assert connection.transaction_entered == 1
    assert connection.transaction_exited == 1
