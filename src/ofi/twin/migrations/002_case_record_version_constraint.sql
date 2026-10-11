-- Add a database invariant for optimistic case versions.
-- Existing case records must satisfy the same non-negative invariant as new
-- records. This migration is safe to re-run after an interrupted deployment.
DO $ofi_migration$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'case_records_version_nonnegative'
          AND conrelid = 'case_records'::regclass
    ) THEN
        ALTER TABLE case_records
            ADD CONSTRAINT case_records_version_nonnegative
            CHECK (version >= 0);
    END IF;
END
$ofi_migration$;
