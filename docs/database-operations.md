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
## Staging migration and recovery rehearsal

Install PostgreSQL client utilities (`pg_dump`, `pg_restore`) in addition to the Python PostgreSQL extra. For encrypted backup output, install the `age` encryption utility too. The CI workflow installs these tools and runs a real dump/restore rehearsal using disposable databases.

For a manual rehearsal, define two database URLs through your secret manager:

- `OFI_RECOVERY_SOURCE_DSN`: a **staging-only** database at either the immediately prior schema version (to rehearse the next migration) or the current schema version (for a recurring restore drill). The harness reads and dumps this database but does not change it.
- `OFI_RECOVERY_RESTORE_DSN`: a separately provisioned, **empty disposable database**. The harness restores a temporary custom-format dump into this database, applies pending migrations to the restored copy, checks readiness, and leaves the upgraded copy available for inspection.

Run:

```bash
ofi-recovery-check
```

The harness fails closed if the source is not at the expected prior migration version, the target is not empty, or both DSNs resolve to the same database identity. It verifies table row counts across all required persistence tables and full-row content fingerprints for core farm/case/service/evidence tables at three points: source snapshot, restored pre-migration copy, and upgraded restore. It also verifies the source schema version did not change. The temporary dump is deleted when the command exits; the restored target database remains at the current version.

The rehearsal performs a database read + backup and populates the target; it does not drop/overwrite target objects and does not mutate the source. Still, use staging data and credentials only. Row fingerprinting scans the configured fingerprint relations and can be expensive for large datasets, so begin with a representative staging clone and plan the rehearsal in an approved maintenance window.

### Rollback proof covered by CI

The PostgreSQL integration suite inserts a deliberately invalid negative case version into a disposable version-1 database and attempts the version-2 constraint migration. The expected constraint failure must roll back the DDL, the legacy checksum backfill, and the version-2 marker in one transaction. The test removes the invalid fixture and confirms the migration then succeeds. It also backs up/restores a populated v1 database, upgrades the restored copy, verifies case/event/farm/crop/observation contents are preserved, and checks the upgraded copy passes the readiness contract.

This is a reproducible staging/CI rehearsal, not evidence that a production backup has been retained, that point-in-time recovery has been tested, or that an actual production incident has been recovered from. Production readiness still requires an approved backup-retention policy, encrypted off-site storage, restore drills on representative data, credentials/network review, and a documented recovery-time/recovery-point objective.

### Scheduled encrypted recovery drill

The `.github/workflows/recovery-drill.yml` workflow can run manually from GitHub Actions or automatically every Monday at 09:00 India time. It skips safely when its required Actions secrets are not configured. To enable it, configure these under **Repository Settings → Secrets and variables → Actions**:

- `OFI_RECOVERY_SOURCE_DSN`: staging database connection string. Use a role with read access sufficient for `pg_dump` and the readiness/fingerprint queries; prefer TLS and least privilege.
- `OFI_RECOVERY_RESTORE_ADMIN_DSN`: connection string to a maintenance database (commonly `postgres`) for a role allowed to create and drop databases. The workflow creates a uniquely named empty database for each run and drops it after the check, even when the check fails.
- `OFI_BACKUP_AGE_RECIPIENT`: an `age1...` public recipient generated with `age-keygen`. Keep the corresponding private identity outside the repository and outside workflow artifacts, in an independently controlled secure location.

The workflow performs a source-to-disposable-target restore and readiness check, encrypts the custom-format dump with `age`, and uploads an artifact containing the **encrypted** dump and JSON report. The temporary plaintext dump is deleted when the command exits. Artifacts are retained by GitHub Actions for 30 days and include a SHA-256 checksum and byte size in the report. Keep the private decryption identity separately; do not store it in the repository or the same artifact store.

An encrypted GitHub Actions artifact is a short-term retained copy, not an independent off-site backup service. A real production backup strategy still needs owner-approved retention, access control, independently stored decryption keys, off-site or provider-native backup storage, restore drills with production-like data, and agreed RPO/RTO targets. Scheduled checks do not mean a production recovery has been proven. The restore-admin role should be confined to a dedicated staging environment and must never be granted authority to drop production databases.
