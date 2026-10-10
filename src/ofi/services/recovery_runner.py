"""Environment-configured CLI entry point for scheduled dispatch recovery.

A deployment supplies a trusted factory that constructs its real repository,
provider-specific reconciliation adapters, and DispatchRecoveryWorker. This
module intentionally contains no provider credentials or infrastructure policy.
"""

from __future__ import annotations

import importlib
import json
import logging
import os
import sys
from typing import Any, Mapping, TextIO

from ofi.services.recovery_health import RecoveryHealthThresholds
from ofi.services.recovery_runtime import run_recovery_cycle


_THRESHOLD_ENV = {
    "warning_errors": "OFI_RECOVERY_WARNING_ERRORS",
    "critical_errors": "OFI_RECOVERY_CRITICAL_ERRORS",
    "warning_unknown": "OFI_RECOVERY_WARNING_UNKNOWN",
    "critical_unknown": "OFI_RECOVERY_CRITICAL_UNKNOWN",
    "warning_pending": "OFI_RECOVERY_WARNING_PENDING",
    "critical_pending": "OFI_RECOVERY_CRITICAL_PENDING",
}


class JsonEventFormatter(logging.Formatter):
    """Serialize recovery events as JSON lines without exception payloads."""

    def format(self, record: logging.LogRecord) -> str:
        event = getattr(record, "ofi_recovery_health", None)
        if isinstance(event, dict):
            payload: dict[str, Any] = event
        else:
            payload = {
                "event": "ofi.recovery.runner",
                "level": record.levelname.lower(),
                "message": record.getMessage(),
            }
            error_type = getattr(record, "error_type", None)
            if error_type:
                payload["error_type"] = error_type
        return json.dumps(payload, separators=(",", ":"), sort_keys=True)


def _positive_int(name: str, environ: Mapping[str, str]) -> int:
    raw = environ.get(name)
    if raw is None or not raw.strip():
        raise ValueError(f"required environment variable is missing: {name}")
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"environment variable must be an integer: {name}") from exc
    if value < 1:
        raise ValueError(f"environment variable must be positive: {name}")
    return value


def _thresholds(environ: Mapping[str, str]) -> RecoveryHealthThresholds:
    values = {
        field: _positive_int(env_name, environ)
        for field, env_name in _THRESHOLD_ENV.items()
    }
    return RecoveryHealthThresholds(**values)


def _load_factory(spec: str):
    module_name, separator, attribute = spec.partition(":")
    if not separator or not module_name.strip() or not attribute.strip():
        raise ValueError(
            "OFI_RECOVERY_FACTORY must use the trusted 'module:callable' form"
        )
    module = importlib.import_module(module_name)
    factory = getattr(module, attribute)
    if not callable(factory):
        raise ValueError("configured recovery factory is not callable")
    return factory


def _logger(stream: TextIO) -> logging.Logger:
    logger = logging.getLogger("ofi.recovery.runner")
    logger.handlers.clear()
    logger.setLevel(logging.INFO)
    logger.propagate = False
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonEventFormatter())
    logger.addHandler(handler)
    return logger


def run_from_environment(
    *,
    environ: Mapping[str, str] | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    """Run one cycle from deployment environment; return a process exit code.

    Exit 0 means the cycle ran and emitted a health event, even if that event is
    warning/critical; the log pipeline should route those states to alerting.
    Exit 2 means configuration or execution failed. Failure logs intentionally
    omit exception messages because those may contain credentials/provider data.
    """
    env = os.environ if environ is None else environ
    out = sys.stdout if stdout is None else stdout
    err = sys.stderr if stderr is None else stderr
    try:
        factory_spec = env.get("OFI_RECOVERY_FACTORY", "").strip()
        if not factory_spec:
            raise ValueError("required environment variable is missing: OFI_RECOVERY_FACTORY")

        thresholds = _thresholds(env)
        stale_after_seconds = _positive_int("OFI_RECOVERY_STALE_AFTER_SECONDS", env)
        limit = _positive_int("OFI_RECOVERY_LIMIT", env)
        factory = _load_factory(factory_spec)
        worker = factory()
        logger = _logger(out)
        run_recovery_cycle(
            worker,
            thresholds=thresholds,
            logger=logger,
            stale_after=__import__("datetime").timedelta(seconds=stale_after_seconds),
            limit=limit,
        )
        return 0
    except Exception as exc:
        payload = {
            "event": "ofi.recovery.runner",
            "level": "error",
            "message": "recovery runner configuration or cycle failed",
            "error_type": type(exc).__name__,
        }
        print(json.dumps(payload, separators=(",", ":"), sort_keys=True), file=err)
        return 2


def main() -> int:
    """Console-script target."""
    return run_from_environment()


if __name__ == "__main__":
    raise SystemExit(main())
