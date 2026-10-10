import pytest
from ofi.services.deployment_recovery import build_worker

def test_factory_requires_database_configuration():
    with pytest.raises(ValueError):
        build_worker(environ={})

def test_factory_requires_provider_configuration():
    with pytest.raises(ValueError):
        build_worker(environ={"OFI_DATABASE_URL": "postgresql://localhost/ofi"})
