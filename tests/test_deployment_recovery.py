import sys
import types
import pytest

from ofi.services.deployment_recovery import build_worker
from ofi.services.dispatch_recovery_worker import DispatchRecoveryWorker
from ofi.services.reconciliation_adapter import ProviderReconciliationAdapter
from ofi.services.transaction_repository import PostgresTransactionRepository

def config():
    return {
        "OFI_DATABASE_URL": "postgresql://localhost/ofi",
        "OFI_RECOVERY_PROVIDER_IDS": "kvk-demo,weather-1",
        "OFI_RECOVERY_PROVIDER_KVK_DEMO_ENDPOINT": "https://provider.example/reconcile",
        "OFI_RECOVERY_PROVIDER_KVK_DEMO_SECRET": "test-value-a",
        "OFI_RECOVERY_PROVIDER_KVK_DEMO_TIMEOUT_SECONDS": "2.5",
        "OFI_RECOVERY_PROVIDER_WEATHER_1_ENDPOINT": "https://weather.example/reconcile",
        "OFI_RECOVERY_PROVIDER_WEATHER_1_SECRET": "test-value-b",
    }

class FakeCursor:
    def __init__(self, tables):
        self.tables = tables
        self.rows = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, query, params):
        self.rows = [(name,) for name in self.tables if name in params]

    def fetchall(self):
        return self.rows

class FakeConnection:
    def __init__(self, tables):
        self.tables = tables

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def cursor(self):
        return FakeCursor(self.tables)

def install_psycopg(monkeypatch, tables=None):
    module = types.ModuleType("psycopg")
    available = tables if tables is not None else {
        "service_transactions",
        "service_transaction_events",
        "service_execution_attempts",
        "service_reconciliation_events",
    }
    module.connect = lambda dsn: FakeConnection(available)
    monkeypatch.setitem(sys.modules, "psycopg", module)

def test_factory_composes_postgres_repository_and_adapters(monkeypatch):
    install_psycopg(monkeypatch)
    worker = build_worker(environ=config())
    assert isinstance(worker, DispatchRecoveryWorker)
    assert isinstance(worker._repository, PostgresTransactionRepository)
    assert set(worker._adapters) == {"kvk-demo", "weather-1"}
    assert all(isinstance(adapter, ProviderReconciliationAdapter) for adapter in worker._adapters.values())
    assert worker._adapters["kvk-demo"].transport.timeout_seconds == 2.5

def test_factory_requires_database_and_provider_settings():
    with pytest.raises(ValueError):
        build_worker(environ={})
    with pytest.raises(ValueError):
        build_worker(environ={"OFI_DATABASE_URL": "postgresql://localhost/ofi"})

def test_factory_rejects_non_https_endpoint(monkeypatch):
    install_psycopg(monkeypatch)
    env = config()
    env["OFI_RECOVERY_PROVIDER_KVK_DEMO_ENDPOINT"] = "http://provider.example/reconcile"
    with pytest.raises(ValueError, match="HTTPS"):
        build_worker(environ=env)

def test_factory_rejects_duplicate_and_colliding_provider_ids(monkeypatch):
    install_psycopg(monkeypatch)
    env = config()
    env["OFI_RECOVERY_PROVIDER_IDS"] = "kvk-demo,kvk-demo"
    with pytest.raises(ValueError, match="unique"):
        build_worker(environ=env)
    env["OFI_RECOVERY_PROVIDER_IDS"] = "kvk-demo,kvk_demo"
    with pytest.raises(ValueError, match="collide"):
        build_worker(environ=env)

def test_factory_rejects_non_positive_timeout(monkeypatch):
    install_psycopg(monkeypatch)
    env = config()
    env["OFI_RECOVERY_PROVIDER_KVK_DEMO_TIMEOUT_SECONDS"] = "0"
    with pytest.raises(ValueError, match="timeout"):
        build_worker(environ=env)

def test_factory_fails_closed_when_database_schema_is_incomplete(monkeypatch):
    install_psycopg(monkeypatch, tables={"service_transactions"})
    with pytest.raises(RuntimeError, match="schema is incomplete"):
        build_worker(environ=config())

def test_factory_checks_database_connectivity_before_returning_worker(monkeypatch):
    module = types.ModuleType("psycopg")
    def unavailable(dsn):
        raise OSError("database unavailable")
    module.connect = unavailable
    monkeypatch.setitem(sys.modules, "psycopg", module)
    with pytest.raises(OSError, match="database unavailable"):
        build_worker(environ=config())
