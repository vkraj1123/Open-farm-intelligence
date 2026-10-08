"""Connection-scoped PostgreSQL transaction context used by repository adapters."""

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Callable, Iterator

_current_connection: ContextVar[Any | None] = ContextVar(
    "ofi_postgres_connection", default=None
)


@contextmanager
def connection_scope(connection_factory: Callable[[], Any]) -> Iterator[Any]:
    """Yield the active UoW connection, or an owned standalone connection."""
    active = _current_connection.get()
    if active is not None:
        yield active
        return

    with connection_factory() as conn:
        yield conn


@contextmanager
def bind_connection(connection: Any) -> Iterator[None]:
    """Bind one connection to the current execution context."""
    token = _current_connection.set(connection)
    try:
        yield
    finally:
        _current_connection.reset(token)
