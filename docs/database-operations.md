# Database schema and readiness operations

## Persistence modes

- With `OFI_DATABASE_URL` unset, the API uses process-local in-memory state. `GET /ready` returns HTTP 200 and identifies `persistence: memory` and `durable: false`.
- With `OFI_DATABASE_URL` configured, the API uses PostgreSQL/PostGIS repositories and the durable comparison ledger. Readiness returns HTTP 200 only when the database connection works, PostGIS is installed, all required persistence relations exist, and the recorded schema version matches the application.
- `GET /health` is a liveness check only. It intentionally does not require database access. Use `GET /ready` for deployment readiness probes.

The readiness response intentionally does not expose the DSN or raw database exception messages. In PostgreSQL mode, a failure returns HTTP 503 and a structured set of checks, including missing relations or version mismatch.

## Apply the schema

Install the optional driver and install the package:

```bash
pip install -e '.[postgres]'
```

Set `OFI_DATABASE_URL` through your deployment secret manager, then run:

```bash
ofi-schema
```

Or supply the URL as a command-line argument when your local workflow requires it:

```bash
ofi-schema --database-url 'postgresql://...'
```

The command applies the idempotent canonical script at `src/ofi/twin/schema.sql`, including the PostGIS extension and the `ofi_schema_migrations` version marker, in a database transaction. It can be rerun safely for the current schema version. It refuses to apply an older application schema if the database's recorded version is newer. The current schema version is **1**.

The database user needs permission to create the PostGIS extension if it is not already installed, and to create/alter the application's tables and indexes. In managed PostgreSQL services, a database administrator may need to enable PostGIS first. Back up production data before applying schema changes, and test migrations against a staging copy.

## Schema-version discipline

The migration registry records the schema revision applied by the canonical schema script. For a future incompatible or additive schema revision:

1. Add the forward-compatible SQL changes and preserve existing data.
2. Increment `CURRENT_SCHEMA_VERSION` in `ofi.services.database_readiness`.
3. Add a new version row at the end of `src/ofi/twin/schema.sql` with a migration name. Do not rewrite old version rows.
4. Add PostgreSQL integration coverage for upgrading from the prior supported schema and verifying the new version.
5. Run standard and PostgreSQL CI; apply in staging before production.

Version 1 is the repository's baseline schema marker; it is not a historical chain of individually replayable SQL files. The full schema script is idempotent bootstrap/upgrade SQL. If the project adopts non-idempotent incremental migrations, those should be separate, ordered migration files rather than edits that rerun data-destructive statements.

## Operational boundary

A successful readiness check confirms connectivity and expected schema objects only. It does not check connection-pool saturation, backups, point-in-time recovery, provider availability, alert delivery, or agricultural model validity. Production operations still require credentials management, network policy, monitoring, backup/recovery drills and staging verification.
