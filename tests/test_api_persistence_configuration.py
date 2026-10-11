"""API persistence selection must fail closed and coordinate PostgreSQL writes."""

import sys
from types import SimpleNamespace

import pytest

import ofi.api as api
from ofi.services.case_manager import CaseManager
from ofi.services.case_repository import InMemoryCaseRepository
from ofi.services.postgres_case_repository import PostgresCaseRepository
from ofi.services.unit_of_work import InMemoryFarmCaseUnitOfWork, PostgresFarmCaseUnitOfWork
from ofi.services.tenant_membership_repository import (
    InMemoryTenantMembershipRepository,
    PostgresTenantMembershipRepository,
)
from ofi.twin.farm_twin import FarmTwinStore
from ofi.twin.postgis import PostGISFarmTwinStore


def test_api_uses_in_memory_repositories_without_database_url(monkeypatch):
    monkeypatch.delenv("OFI_DATABASE_URL", raising=False)

    manager = api._build_case_manager()

    assert isinstance(manager, CaseManager)
    assert isinstance(manager.store, InMemoryCaseRepository)
    assert isinstance(manager.farm_twin, FarmTwinStore)
    assert isinstance(manager.unit_of_work, InMemoryFarmCaseUnitOfWork)


def test_api_uses_postgres_repositories_with_one_shared_connection_factory(monkeypatch):
    connected_dsns = []
    fake_psycopg = SimpleNamespace(
        connect=lambda dsn: connected_dsns.append(dsn) or object()
    )
    monkeypatch.setenv("OFI_DATABASE_URL", "postgresql://localhost/ofi")
    monkeypatch.setitem(sys.modules, "psycopg", fake_psycopg)

    manager = api._build_case_manager()

    assert isinstance(manager.unit_of_work, PostgresFarmCaseUnitOfWork)
    assert isinstance(manager.store, PostgresCaseRepository)
    assert isinstance(manager.farm_twin, PostGISFarmTwinStore)
    factory = manager.unit_of_work._connection_factory
    assert manager.store._connection_factory is factory
    assert manager.farm_twin._connection_factory is factory
    factory()
    assert connected_dsns == ["postgresql://localhost/ofi"]


def test_configured_database_without_psycopg_fails_closed(monkeypatch):
    monkeypatch.setenv("OFI_DATABASE_URL", "postgresql://localhost/ofi")
    monkeypatch.setitem(sys.modules, "psycopg", None)

    with pytest.raises(RuntimeError, match="install the postgres extra"):
        api._build_case_manager()


def test_api_uses_shared_membership_repository_for_postgres_and_memory(monkeypatch):
    monkeypatch.delenv("OFI_DATABASE_URL", raising=False)
    assert isinstance(api._build_tenant_membership_repository(), InMemoryTenantMembershipRepository)

    connected_dsns = []
    fake_psycopg = SimpleNamespace(
        connect=lambda dsn: connected_dsns.append(dsn) or object()
    )
    monkeypatch.setenv("OFI_DATABASE_URL", "postgresql://localhost/ofi")
    monkeypatch.setitem(sys.modules, "psycopg", fake_psycopg)
    memberships = api._build_tenant_membership_repository()
    assert isinstance(memberships, PostgresTenantMembershipRepository)
    memberships._connection_factory()
    assert connected_dsns == ["postgresql://localhost/ofi"]
