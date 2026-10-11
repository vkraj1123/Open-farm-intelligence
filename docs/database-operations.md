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

The `ofi-schema` command applies numbered SQL files from `src/ofi/twin/migrations/` in a single database transaction. The current registry contains:

| Version | Migration | Purpose |
|---:|---|---|
| 1 | `001_initial_postgis_case_service_and_evidence_schema.sql` | Initial farm/case/service/evidence schema and PostGIS support |
| 2 | `002_case_record_version_constraint.sql` | Enforce non-negative optimistic versions on case records |

Each migration records its name and SHA-256 checksum in `ofi_schema_migrations`. Applied migrations are immutable: re-running is a no-op when the migration record matches; a checksum or name mismatch fails closed. The runner serializes concurrent migration processes with a PostgreSQL advisory transaction lock and records each version only in the transaction that applies its SQL. It refuses to run if it sees a newer version or a missing predecessor.

The `src/ofi/twin/schema.sql` file remains as a compatibility/bootstrap script representing version 1, so older development and integration setups can create the baseline schema. Use `ofi-schema` for application installs and upgrades; after applying only the baseline script, `/ready` correctly reports an outdated schema until pending migrations are applied. The runner upgrades an existing v1 marker that predates checksums and applies the pending v2 migration.

The database user needs permission to create the PostGIS extension if it is not already installed, and to create/alter the application's tables and indexes. In managed PostgreSQL services, a database administrator may need to enable PostGIS first. Back up production data before applying schema changes, and test migrations against a staging copy.

## Adding a schema migration

1. Create the next contiguous, numerically prefixed SQL file, for example `003_add_observation_index.sql`. Do not edit files already applied to shared databases.
2. Add a `SchemaMigration` entry to `MIGRATIONS` in `src/ofi/twin/migration_registry.py`, with the next version number, stable name and filename. The current version is derived from the last registry entry; readiness uses that registry value.
3. Make the SQL forward-compatible, safe for existing data, and transactional. For potentially large production tables, assess table locks and use a staged/concurrent migration strategy where appropriate.
4. Add PostgreSQL integration tests that start from the previous supported version, apply the migration, verify the resulting schema, and confirm a second run is a no-op.
5. Run standard and PostgreSQL CI. Test on a staging copy and verify backups/recovery before applying to production.

The registry validates contiguous numbering and unique migration names/filenames at import time. The runner stores SHA-256 checksums and rejects drift in applied SQL. If an already-released migration is wrong, create a new corrective migration instead of rewriting history.
## Operational boundary

A successful readiness check confirms connectivity and expected schema objects only. It does not check connection-pool saturation, backups, point-in-time recovery, provider availability, alert delivery, or agricultural model validity. Production operations still require credentials management, network policy, monitoring, backup/recovery drills and staging verification.
