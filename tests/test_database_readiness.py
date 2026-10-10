"""Readiness checks are sanitized and reflect the configured storage mode."""

from fastapi.testclient import TestClient

import ofi.api as api
from ofi.services.database_readiness import (
    CURRENT_SCHEMA_VERSION,
    REQUIRED_RELATIONS,
    check_persistence_readiness,
)


class FakeCursor:
    def __init__(self, *, postgis=True, version=CURRENT_SCHEMA_VERSION, relations=None):
        self.postgis = postgis
        self.version = version
        self.relations = set(relations if relations is not None else REQUIRED_RELATIONS)
        self.relations.add("ofi_schema_migrations")
        self.query = ""

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, sql, params=None):
        self.query = " ".join(sql.split())

    def fetchone(self):
        if "pg_extension" in self.query:
            return (self.postgis,)
        if "max(version)" in self.query:
            return (self.version,)
        raise AssertionError(f"unexpected fetchone query: {self.query}")

    def fetchall(self):
        if "information_schema.tables" in self.query:
            return [(relation,) for relation in self.relations]
        raise AssertionError(f"unexpected fetchall query: {self.query}")


class FakeConnection:
    def __init__(self, cursor):
        self._cursor = cursor

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def cursor(self):
        return self._cursor


def factory_for(**kwargs):
    cursor = FakeCursor(**kwargs)
    return lambda: FakeConnection(cursor)


def test_memory_mode_is_ready_but_explicitly_non_durable():
    report = check_persistence_readiness(None)

    assert report["status"] == "ready"
    assert report["persistence"] == "memory"
    assert report["durable"] is False


def test_postgres_readiness_requires_postgis_relations_and_current_version():
    report = check_persistence_readiness(factory_for())

    assert report["status"] == "ready"
    assert report["persistence"] == "postgresql"
    assert report["durable"] is True
    assert report["checks"]["schema_version"]["current"] == CURRENT_SCHEMA_VERSION
    assert report["checks"]["missing_relations"] == []


def test_postgres_readiness_fails_when_schema_is_incomplete_or_outdated():
    relations = set(REQUIRED_RELATIONS) - {"case_events"}
    report = check_persistence_readiness(
        factory_for(postgis=False, version=CURRENT_SCHEMA_VERSION - 1, relations=relations)
    )

    assert report["status"] == "not_ready"
    assert report["checks"]["postgis"] is False
    assert "case_events" in report["checks"]["missing_relations"]
    assert report["checks"]["schema_version"]["ready"] is False


def test_connection_failure_is_redacted_from_readiness_response():
    def fail():
        raise RuntimeError("postgresql://user:secret@db.example/ofi")

    report = check_persistence_readiness(fail)

    assert report["status"] == "not_ready"
    assert report["checks"]["database_connection"] is False
    assert "secret" not in repr(report)
    assert "db.example" not in repr(report)


def test_ready_endpoint_returns_503_for_unready_persistence(monkeypatch):
    client = TestClient(api.app)
    monkeypatch.setattr(
        api,
        "check_persistence_readiness",
        lambda factory: {
            "status": "not_ready",
            "persistence": "postgresql",
            "durable": True,
            "checks": {"database_connection": False},
        },
    )
    monkeypatch.setattr(api, "postgres_connection_factory", lambda: None)

    response = client.get("/ready")

    assert response.status_code == 503
    assert response.json()["status"] == "not_ready"


def test_health_remains_a_liveness_check_when_readiness_is_separate():
    client = TestClient(api.app)

    assert client.get("/health").status_code == 200
