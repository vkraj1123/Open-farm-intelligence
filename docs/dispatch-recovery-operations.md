# Dispatch Recovery Operations Runbook

## Status and scope

This runbook defines the deployment contract for the recovery runtime in:

- `ofi.services.dispatch_recovery_worker.DispatchRecoveryWorker`
- `ofi.services.recovery_health`
- `ofi.services.recovery_observability`
- `ofi.services.recovery_runtime.run_recovery_cycle`

It is not a complete production deployment, but the repository now includes a reference composition root: `ofi.services.deployment_recovery:build_worker`. It constructs `PostgresTransactionRepository` and one `ProviderReconciliationAdapter` per configured provider using `HttpsReconciliationTransport`. Deployments still own database provisioning/migrations, credentials, provider endpoint readiness, scheduling, concurrency controls, and alert delivery.

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

For a concrete PostgreSQL + HTTPS setup, set `OFI_RECOVERY_FACTORY=ofi.services.deployment_recovery:build_worker` and also configure:

| Variable | Meaning |
|---|---|
| `OFI_DATABASE_URL` | PostgreSQL DSN for the transaction repository |
| `OFI_RECOVERY_PROVIDER_IDS` | Comma-separated provider IDs, for example `kvk-demo,weather-1` |
| `OFI_RECOVERY_PROVIDER_<NORMALIZED_ID>_ENDPOINT` | Absolute HTTPS reconciliation endpoint for that provider |
| `OFI_RECOVERY_PROVIDER_<NORMALIZED_ID>_SECRET` | Provider HMAC-SHA256 verification secret, supplied through secret management |
| `OFI_RECOVERY_PROVIDER_<NORMALIZED_ID>_TIMEOUT_SECONDS` | Optional positive request timeout; defaults to 5 seconds |

Provider IDs are normalized to uppercase environment suffixes, with punctuation mapped to underscores; IDs that collide after normalization are rejected. The factory requires the optional `postgres` dependency (`pip install .[postgres]`) and a database whose transaction/attempt/reconciliation schema has already been migrated. At startup it opens a PostgreSQL connection and checks for `service_transactions`, `service_transaction_events`, `service_execution_attempts`, and `service_reconciliation_events`; it fails early if connectivity or required tables are missing. This readiness check is not a substitute for migrations or repository integration tests. The factory does not create schema or provision provider credentials. Each endpoint must return the exact signed JSON response format accepted by `ProviderReconciliationAdapter`; a reachable URL alone does not establish that the provider is authoritative.

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


## 7. PostgreSQL integration verification

The PostgreSQL CI job sets OFI_POSTGRES_DSN and runs tests/test_postgres_recovery_factory_integration.py. That test applies src/ofi/twin/schema.sql to the dedicated CI database, builds the concrete deployment factory, performs transaction/attempt persistence through the PostgreSQL repository, and runs a bounded worker cycle. The configured HTTPS endpoint is a placeholder and is not contacted when there are no recovery candidates.

A passing CI integration test proves that the checked-in schema and repository/factory wiring work together in the CI PostgreSQL environment. It does **not** prove connectivity to a deployment's staging database, correctness of a real provider's signed reconciliation responses, provider-side idempotency, scheduler behavior, or end-to-end alert delivery. Those require separate staging credentials and operational verification.


## 8. Read-only live-provider contract check

The opt-in test `tests/test_live_provider_reconciliation_contract.py` checks a real configured staging reconciliation endpoint without writing to the OFI repository or dispatching actions. By default it is skipped unless all required `OFI_STAGING_*` variables are set. To make a dedicated staging job fail closed instead of silently skipping when its configuration is missing, set `OFI_STAGING_REQUIRED=1`; this setting contains no secret.

Required variables:

- `OFI_STAGING_PROVIDER_ID`: exact configured provider identity.
- `OFI_STAGING_RECONCILIATION_ENDPOINT`: absolute HTTPS reconciliation endpoint.
- `OFI_STAGING_RECONCILIATION_SECRET`: HMAC-SHA256 verification secret from a secret manager.
- `OFI_STAGING_RECONCILIATION_CASES`: JSON array of four provider-issued test cases covering `executed`, `not_executed`, `pending`, and `unknown`. Each object has `transaction_id`, `attempt_id`, and `expected_status`.

Run with `pytest -q tests/test_live_provider_reconciliation_contract.py` in an environment where these variables are injected securely. In a dedicated staging validation job, set `OFI_STAGING_REQUIRED=1` so absent configuration is a failure rather than a skip. Do not place secrets in shell history, CI logs, issue comments, or committed files; inject them through the deployment or CI secret manager. Use only provider-approved staging IDs; the test calls the configured read-only reconciliation endpoint and never dispatches or changes OFI state.

A pass demonstrates that the configured endpoint conforms to OFI's signed payload, exact attempt identity, freshness and status contract for those examples. It does not prove that the provider's status claims are authoritative, nor validate provider-side idempotency, production network behavior, scheduler overlap controls or alert delivery. Those remain separate staging acceptance checks.


## 9. Kubernetes CronJob starter template

A non-production starter manifest is available at `deploy/kubernetes/ofi-recovery-cronjob.yaml`. It configures `concurrencyPolicy: Forbid`, a bounded active deadline, finite job history, a non-root container security context, and separate ConfigMap/Secret references.

Before applying it:

1. Build and publish the OFI image, then replace the placeholder image with a pinned image tag or digest.
2. Replace the sample provider ID and HTTPS endpoint with the real provider configuration.
3. Create `ofi-recovery-secrets` through the cluster's secret-management workflow. The example expects `OFI_DATABASE_URL` and `OFI_RECOVERY_PROVIDER_PROVIDER_DEMO_SECRET`; never commit a populated Secret manifest.
4. Review the sample hourly schedule, timeouts, resource bounds, retry budget and health thresholds against the provider contract and service objectives.
5. Confirm the database schema is migrated and reachable from the cluster before enabling the CronJob.
6. Validate with a staging namespace and synthetic/non-mutating reconciliation cases, then verify logs and alert delivery.

The manifest is a template, not a deployable release: its image and endpoint are intentionally placeholders. Kubernetes `concurrencyPolicy: Forbid` prevents overlapping Jobs created by this CronJob, but it is not a distributed lock against manual invocations or other schedulers. Do not run multiple independent schedulers for the same recovery workload without an additional coordination design. A Job completion does not prove provider correctness or alert delivery.
