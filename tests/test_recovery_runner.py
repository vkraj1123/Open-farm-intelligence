import io
import json
import sys
import types
from datetime import timedelta

from ofi.services.dispatch_recovery_worker import (
    DispatchRecoveryItem,
    DispatchRecoveryReport,
)
from ofi.services import recovery_runner
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
    def __init__(self, report=None):
        self.kwargs = None
        self.report = report or DispatchRecoveryReport(candidates=0, items=())

    def run_once(self, **kwargs):
        self.kwargs = kwargs
        return self.report


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



def test_runner_delivers_warning_to_configured_webhook(monkeypatch):
    worker = FakeWorker(
        DispatchRecoveryReport(
            candidates=1,
            items=(
                DispatchRecoveryItem(
                    attempt_id="private-attempt",
                    provider_id="private-provider",
                    status="unknown",
                    error="private provider response",
                ),
            ),
        )
    )
    module = types.ModuleType("ofi_test_alert_factory")
    module.build_worker = lambda: worker
    monkeypatch.setitem(sys.modules, "ofi_test_alert_factory", module)
    calls = []

    class FakeWebhook:
        def __init__(self, endpoint, secret, *, timeout_seconds):
            assert endpoint == "https://alerts.example.test/ofi"
            assert secret == "s" * 40
            assert timeout_seconds == 3.5

        def send(self, event):
            calls.append(event)
            return types.SimpleNamespace(event_id="event-digest", status_code=202)

    monkeypatch.setattr(recovery_runner, "RecoveryAlertWebhook", FakeWebhook)
    env = base_env("ofi_test_alert_factory:build_worker")
    env.update(
        {
            "OFI_RECOVERY_ALERT_ENDPOINT": "https://alerts.example.test/ofi",
            "OFI_RECOVERY_ALERT_SECRET": "s" * 40,
            "OFI_RECOVERY_ALERT_TIMEOUT_SECONDS": "3.5",
        }
    )
    stdout, stderr = io.StringIO(), io.StringIO()

    code = run_from_environment(environ=env, stdout=stdout, stderr=stderr)

    assert code == 0
    assert stderr.getvalue() == ""
    assert len(calls) == 1
    assert calls[0]["status"] == "warning"
    lines = stdout.getvalue().splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["status"] == "warning"
    assert json.loads(lines[1]) == {
        "event": "ofi.recovery.alert_delivery",
        "status": "delivered",
        "health_status": "warning",
        "event_id": "event-digest",
        "http_status": 202,
    }
    combined = stdout.getvalue()
    assert "private-attempt" not in combined
    assert "private-provider" not in combined
    assert "private provider response" not in combined


def test_runner_does_not_send_webhook_for_healthy_cycle(monkeypatch):
    worker = FakeWorker()
    module = types.ModuleType("ofi_test_healthy_alert_factory")
    module.build_worker = lambda: worker
    monkeypatch.setitem(sys.modules, "ofi_test_healthy_alert_factory", module)
    calls = []

    class FakeWebhook:
        def __init__(self, *_args, **_kwargs):
            pass

        def send(self, event):
            calls.append(event)
            return types.SimpleNamespace(event_id="event-digest", status_code=200)

    monkeypatch.setattr(recovery_runner, "RecoveryAlertWebhook", FakeWebhook)
    env = base_env("ofi_test_healthy_alert_factory:build_worker")
    env.update(
        {
            "OFI_RECOVERY_ALERT_ENDPOINT": "https://alerts.example.test/ofi",
            "OFI_RECOVERY_ALERT_SECRET": "s" * 40,
        }
    )
    stdout, stderr = io.StringIO(), io.StringIO()

    code = run_from_environment(environ=env, stdout=stdout, stderr=stderr)

    assert code == 0
    assert calls == []
    assert len(stdout.getvalue().splitlines()) == 1
    assert stderr.getvalue() == ""


def test_runner_requires_alert_endpoint_and_secret_as_a_pair():
    env = base_env()
    env["OFI_RECOVERY_ALERT_ENDPOINT"] = "https://alerts.example.test/ofi"
    stdout, stderr = io.StringIO(), io.StringIO()

    code = run_from_environment(environ=env, stdout=stdout, stderr=stderr)

    assert code == 2
    assert stdout.getvalue() == ""
    error = json.loads(stderr.getvalue())
    assert error["error_type"] == "ValueError"
    assert "must be configured together" not in stderr.getvalue()



def test_runner_returns_failure_when_alert_delivery_fails(monkeypatch):
    worker = FakeWorker(
        DispatchRecoveryReport(
            candidates=1,
            items=(
                DispatchRecoveryItem(
                    attempt_id="private-attempt",
                    provider_id="private-provider",
                    status="unknown",
                ),
            ),
        )
    )
    module = types.ModuleType("ofi_test_alert_failure_factory")
    module.build_worker = lambda: worker
    monkeypatch.setitem(sys.modules, "ofi_test_alert_failure_factory", module)

    class FailingWebhook:
        def __init__(self, *_args, **_kwargs):
            pass

        def send(self, _event):
            raise recovery_runner.RecoveryAlertDeliveryError(
                "receiver private endpoint and secret"
            )

    monkeypatch.setattr(recovery_runner, "RecoveryAlertWebhook", FailingWebhook)
    env = base_env("ofi_test_alert_failure_factory:build_worker")
    env.update(
        {
            "OFI_RECOVERY_ALERT_ENDPOINT": "https://alerts.example.test/ofi",
            "OFI_RECOVERY_ALERT_SECRET": "s" * 40,
        }
    )
    stdout, stderr = io.StringIO(), io.StringIO()

    code = run_from_environment(environ=env, stdout=stdout, stderr=stderr)

    assert code == 2
    assert json.loads(stdout.getvalue())["status"] == "warning"
    error = json.loads(stderr.getvalue())
    assert error["event"] == "ofi.recovery.runner"
    assert error["error_type"] == "RecoveryAlertDeliveryError"
    assert "receiver private endpoint" not in stderr.getvalue()
    assert "s" * 40 not in stderr.getvalue()
