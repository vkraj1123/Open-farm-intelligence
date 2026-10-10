# Dispatch Recovery Operations Runbook

## Status and scope

This runbook defines the deployment contract for the recovery runtime in:

- `ofi.services.dispatch_recovery_worker.DispatchRecoveryWorker`
- `ofi.services.recovery_health`
- `ofi.services.recovery_observability`
- `ofi.services.recovery_runtime.run_recovery_cycle`

It is **not** a ready-to-run production deployment. The repository does not yet provide a universal composition root that constructs the production transaction repository, provider-specific reconciliation adapters, secrets, and logger. A deployment must supply those dependencies before scheduling a cycle.

The runtime performs one bounded recovery pass, assesses the report against explicitly supplied thresholds, and emits one structured log event. It does not create retries, resend actions, configure a scheduler, or deliver notifications to an external alert receiver.

The package exposes the `ofi-recovery` console command. It loads a trusted factory from `OFI_RECOVERY_FACTORY=package.module:callable`; that no-argument callable must return a configured `DispatchRecoveryWorker` with real repository and reconciliation adapters. The runner reads all thresholds and bounds from required environment variables, emits JSON Lines to stdout, and exits with code 2 on configuration/runtime failure. Health status `warning` or `critical` is a successful cycle with a corresponding event; alert delivery is the log pipeline's responsibility.

## 1. Required deployment wiring

A deployment-owned runner must:

1. Load secrets from the deployment's secret manager; never commit provider secrets.
2. Construct the repository connected to the intended database.
3. Construct an explicit mapping of provider IDs to authenticated reconciliation adapters.
4. Construct `DispatchRecoveryWorker(repository=..., adapters=...)`.
5. Load explicit `RecoveryHealthThresholds` from reviewed deployment configuration.
6. Call `run_recovery_cycle(worker, thresholds=..., logger=...)` once.
7. Return a non-zero process exit code for an uncaught runner/configuration failure. A health assessment of `warning` or `critical` is represented in the structured event and should be translated to deployment-specific alerting by the runner or log pipeline.

Do not point the runner at mock providers or an in-memory repository in a production environment. Fail closed if required provider adapters, database connectivity, or threshold configuration is missing.


### CLI environment variables

The runner requires these deployment variables; it deliberately provides no universal threshold defaults:

| Variable | Meaning |
|---|---|
| `OFI_RECOVERY_FACTORY` | Trusted `module:callable` factory returning a configured recovery worker |
| `OFI_RECOVERY_STALE_AFTER_SECONDS` | Positive stale-claim age threshold in seconds |
| `OFI_RECOVERY_LIMIT` | Positive maximum candidate count per run |
| `OFI_RECOVERY_WARNING_ERRORS` / `OFI_RECOVERY_CRITICAL_ERRORS` | Per-run reconciliation error thresholds |
| `OFI_RECOVERY_WARNING_UNKNOWN` / `OFI_RECOVERY_CRITICAL_UNKNOWN` | Per-run unresolved-unknown thresholds |
| `OFI_RECOVERY_WARNING_PENDING` / `OFI_RECOVERY_CRITICAL_PENDING` | Per-run pending-reconciliation thresholds |

Example scheduler command after installing the package and configuring the variables:

```bash
ofi-recovery
```

The factory module must be trusted deployment code and should obtain credentials from a secret manager. Never put secrets into the factory path, command line, repository, or logs. Exit code 0 means a cycle ran and emitted a health event; route warning/critical JSON events through the log pipeline. Exit code 2 indicates configuration or cycle failure. A scheduler should prevent overlapping invocations and configure bounded process runtime.

## 2. Scheduler contract

Use an external scheduler appropriate to the hosting environment (for example, a managed job scheduler, Kubernetes CronJob, or systemd timer). The scheduled target must be the deployment-owned runner described above, not the general-purpose FastAPI process.

Required behavior:

- Run one bounded cycle per invocation.
- Set a maximum runtime and ensure the process is terminated or marked failed if it exceeds that budget.
- Prevent overlapping runs, either through scheduler concurrency controls or a durable deployment-level lock.
- Use a single process clock in UTC for the cycle.
- Record the runner exit status and retain structured logs.
- Make the schedule configurable per deployment; there is no universally correct interval for every provider or service.
- Ensure the schedule interval, timeout, and reconciliation provider's freshness window are compatible. A timeout must not be interpreted as proof that an external action did not execute.

### Choosing cadence

Choose cadence from the provider's operational contract and the acceptable time-to-reconciliation. Start conservatively in a staging environment and measure:

- cycle duration (including provider latency);
- number of candidates per run;
- reconciliation errors;
- unresolved `unknown` attempts;
- `pending` reconciliations;
- repeated unknown attempts across successive runs.

Do not choose a cadence solely from the default `stale_after=5 minutes`. That value is an API default, not a production recommendation. Set `stale_after` and `limit` explicitly after measuring the deployment's dispatch latency and backlog.

## 3. Threshold configuration

`RecoveryHealthThresholds` requires explicit positive thresholds for warning and critical levels for:

- reconciliation errors;
- unresolved unknown attempts;
- pending reconciliations.

The assessment is per run. It does not currently calculate age of the oldest unresolved attempt, queue backlog, error rate over a time window, or trends across runs. Thresholds therefore need to be supplemented with database/queue-age monitoring if those signals are important to the service-level objective.

Thresholds are deployment policy, not agronomic or universal constants. Review them against expected traffic, provider latency, and the impact of an unresolved action. Do not copy arbitrary example numbers into production without reviewing them.

## 4. Alert routing

The logger emits an event named `ofi.dispatch_recovery.health` with:

- UTC `observed_at`;
- `healthy`, `warning`, or `critical` status;
- low-cardinality per-run metrics;
- alert codes, severity, observed count, and threshold.

Log levels are:

| Health status | Python log level | Operational treatment |
|---|---|---|
| `healthy` | INFO | Retain for baseline/diagnostics |
| `warning` | WARNING | Investigate and watch for persistence |
| `critical` | ERROR | Page or notify the on-call owner according to the service's incident policy |

These are routing recommendations, not a configured alert integration. Configure the deployment's log pipeline or runner to parse the structured `ofi_recovery_health` extra field (or serialize the returned `event` object) and route warning/critical signals to the chosen receiver. Test delivery using synthetic events before relying on it.

The health event intentionally excludes provider IDs, transaction IDs, attempt IDs, raw provider response bodies, and exception text. Keep high-cardinality identifiers and sensitive provider details out of metric labels and alert titles. If per-attempt investigation is required, use the access-controlled audit/reconciliation records rather than widening the aggregate health event.

## 5. Ambiguous outcomes and safe recovery

The following states must remain distinct:

- `dispatching`: the durable local claim was written before the provider call; a process crash can leave external execution ambiguous.
- `unknown`: current evidence cannot establish whether the external action executed.
- `pending`: the provider has not established a final execution outcome.
- `not_executed`: only authenticated, fresh, provider-bound reconciliation evidence for the exact transaction and attempt can establish this result for retry eligibility.
- `executed`: provider reconciliation confirms execution according to the adapter's contract.

Operational rules:

1. Never translate a timeout, missing callback, worker crash, or adapter exception into `not_executed`.
2. Never manually reset an ambiguous attempt to `ready` or resend it to "see if it works".
3. Repeated `unknown` or `pending` results should raise an operational investigation, not trigger an automatic retry.
4. Retry preparation must go through the guarded retry orchestration path after authoritative non-execution evidence has been durably applied.
5. A provider adapter must document what each status means and how its evidence is authenticated, freshness-checked, correlated, and deduplicated.

## 6. Timeout and backoff policy

There are two separate concerns:

- **Scheduler retry:** retrying a failed runner invocation is acceptable only after considering whether the previous invocation may still be active. Prevent overlap and use bounded infrastructure-level retry/backoff.
- **Provider reconciliation retry:** the worker leaves unresolved attempts eligible for a later scheduled pass. Tune the cadence and provider request timeout to avoid hammering an unavailable provider.

Use bounded exponential backoff with jitter in the deployment scheduler or adapter where supported, with a maximum delay and a clear terminal alert condition. Do not implement backoff by changing the attempt's external execution state to failed. A failed reconciliation request is a failure to observe state, not proof of action failure.

The current worker does not itself define provider-specific request timeouts or a retry schedule. Those belong in the adapter and deployment runner and must be tested with network timeout and process-crash scenarios.

## 7. Runbook for an alert

### Warning

1. Inspect the event's `alerts[].code` and metrics.
2. Compare with recent cycles; determine whether the signal is transient or increasing.
3. Check provider health, credentials, rate limits, and reconciliation endpoint latency.
4. Confirm the next scheduled pass is running and not overlapping.

### Critical or persistent unknown

1. Treat the external action as ambiguous until authoritative evidence resolves it.
2. Inspect the access-controlled attempt audit and provider reconciliation receipt for the exact attempt.
3. Check provider status through the documented authoritative endpoint or provider support path.
4. Do not resend or create a retry manually.
5. If the provider confirms non-execution, apply evidence through the supported reconciliation path and then use guarded retry preparation.
6. Record incident findings and any provider contract changes.

## 8. Pre-production checklist

- [ ] Production repository and migrations are configured and backed up.
- [ ] Real reconciliation adapters are registered for every dispatch provider.
- [ ] Provider identity, key rotation, freshness, correlation, and event idempotency are tested.
- [ ] Provider-supported external idempotency behavior is verified with the provider.
- [ ] Provider network timeouts and runner maximum runtime are explicit.
- [ ] Scheduler concurrency/overlap policy is enabled.
- [ ] `stale_after`, `limit`, cadence, and health thresholds are reviewed.
- [ ] Structured logs are retained and warning/critical alerts reach the on-call receiver.
- [ ] A synthetic alert has been delivered end-to-end.
- [ ] Crash-after-claim, provider-timeout, duplicate-callback, and unresolved-reconciliation scenarios have been exercised.
- [ ] A named operational owner and escalation path exist.

Passing this checklist is a deployment-specific decision. The existence of the runbook or runtime function alone does not make OFI production-ready.
