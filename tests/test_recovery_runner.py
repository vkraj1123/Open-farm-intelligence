import io
import json
import sys
import types
from datetime import timedelta

from ofi.services.dispatch_recovery_worker import DispatchRecoveryReport
from ofi.services.recovery_runner import run_from_environment


def base_env(factory="ofi_test_factory:build_worker"):
    return {
        "OFI_RECOVERY_FACTORY": factory,
        "OFI_RECOVERY_STALE_AFTER_SECONDS": "300",
        "OFI_RECOVERY_LIMIT": "25",
        "OFI_RECOVERY_WARNING_ERRORS": "1",
        "OFI_RECOVERY_CRITICAL_ERRORS": "3",
        "OFI_RECOVERY_WARNING_UNKNOWN": "1",
        "OFI_RECOVERY_CRITICAL_UNKNOWN": "3",
        "OFI_RECOVERY_WARNING_PENDING": "2",
        "OFI_RECOVERY_CRITICAL_PENDING": "4",
    }


class FakeWorker:
    def __init__(self):
        self.kwargs = None

    def run_once(self, **kwargs):
        self.kwargs = kwargs
        return DispatchRecoveryReport(candidates=0, items=())


def test_runner_loads_trusted_factory_and_emits_json_health_event(monkeypatch):
    worker = FakeWorker()
    module = types.ModuleType("ofi_test_factory")
    module.build_worker = lambda: worker
    monkeypatch.setitem(sys.modules, "ofi_test_factory", module)
    stdout, stderr = io.StringIO(), io.StringIO()

    code = run_from_environment(
        environ=base_env(),
        stdout=stdout,
        stderr=stderr,
    )

    assert code == 0
    assert stderr.getvalue() == ""
    event = json.loads(stdout.getvalue())
    assert event["event"] == "ofi.dispatch_recovery.health"
    assert event["status"] == "healthy"
    assert event["metrics"]["candidates"] == 0
    assert worker.kwargs["stale_after"] == timedelta(seconds=300)
    assert worker.kwargs["limit"] == 25


def test_runner_requires_explicit_threshold_configuration():
    env = base_env()
    del env["OFI_RECOVERY_WARNING_UNKNOWN"]
    stdout, stderr = io.StringIO(), io.StringIO()

    code = run_from_environment(environ=env, stdout=stdout, stderr=stderr)

    assert code == 2
    assert stdout.getvalue() == ""
    error = json.loads(stderr.getvalue())
    assert error["error_type"] == "ValueError"
    assert error["message"] == "recovery runner configuration or cycle failed"
    assert "OFI_RECOVERY_WARNING_UNKNOWN" not in stderr.getvalue()


def test_runner_failure_does_not_log_exception_message(monkeypatch):
    module = types.ModuleType("ofi_test_failure_factory")

    def fail():
        raise RuntimeError("provider-secret=do-not-log")

    module.build_worker = fail
    monkeypatch.setitem(sys.modules, "ofi_test_failure_factory", module)
    stdout, stderr = io.StringIO(), io.StringIO()

    code = run_from_environment(
        environ=base_env("ofi_test_failure_factory:build_worker"),
        stdout=stdout,
        stderr=stderr,
    )

    assert code == 2
    assert "provider-secret" not in stderr.getvalue()
    error = json.loads(stderr.getvalue())
    assert error["event"] == "ofi.recovery.runner"
    assert error["error_type"] == "RuntimeError"


def test_runner_rejects_non_positive_runtime_limits():
    env = base_env()
    env["OFI_RECOVERY_LIMIT"] = "0"
    stdout, stderr = io.StringIO(), io.StringIO()

    code = run_from_environment(environ=env, stdout=stdout, stderr=stderr)

    assert code == 2
    assert json.loads(stderr.getvalue())["error_type"] == "ValueError"
