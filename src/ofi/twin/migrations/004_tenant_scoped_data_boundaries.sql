-- Version 4: introduce a signed tenant boundary for farm/case data,
-- evidence comparison policies/assessments, and consent grants.
--
-- Existing records are placed in an explicit legacy tenant so no inferred
-- external membership is granted during upgrade. Operators must map legacy
-- records to real tenants through an audited administrative process.

-- Temporarily remove append-only triggers while adding the migration-owned
-- tenant columns. The triggers are recreated at the end of this migration.
DROP TRIGGER IF EXISTS ofi_consent_grant_immutable_trigger ON ofi_consent_grants;
DROP TRIGGER IF EXISTS evidence_comparison_policy_versions_immutable_trigger
    ON evidence_comparison_policy_versions;
DROP TRIGGER IF EXISTS evidence_comparison_assessments_immutable_trigger
    ON evidence_comparison_assessments;
DROP TRIGGER IF EXISTS farms_tenant_immutable_trigger ON farms;
DROP TRIGGER IF EXISTS case_records_tenant_immutable_trigger ON case_records;
DROP TRIGGER IF EXISTS evidence_comparison_policy_heads_tenant_immutable_trigger
    ON evidence_comparison_policy_heads;

-- Farm tenant identity is the parent boundary for parcels, crops, observations,
-- land parties and production contracts (all reference globally unique farm IDs).
ALTER TABLE farms ADD COLUMN IF NOT EXISTS tenant_id TEXT;
UPDATE farms
SET tenant_id = 'legacy:default'
WHERE tenant_id IS NULL OR btrim(tenant_id) = '';
ALTER TABLE farms ALTER COLUMN tenant_id SET DEFAULT 'legacy:default';
ALTER TABLE farms ALTER COLUMN tenant_id SET NOT NULL;
ALTER TABLE farms DROP CONSTRAINT IF EXISTS farms_tenant_id_nonblank;
ALTER TABLE farms ADD CONSTRAINT farms_tenant_id_nonblank
    CHECK (btrim(tenant_id) <> '');
CREATE INDEX IF NOT EXISTS farms_tenant_farmer_created
    ON farms (tenant_id, farmer_id, created_at DESC, id);

-- Case records carry a tenant column separate from their JSON snapshot. Farm
-- storage is authoritative for cases whose farm already exists.
ALTER TABLE case_records ADD COLUMN IF NOT EXISTS tenant_id TEXT;
UPDATE case_records AS c
SET tenant_id = COALESCE(
    (
        SELECT f.tenant_id
        FROM farms AS f
        WHERE f.id = c.case_data #>> '{farm,id}'
    ),
    NULLIF(c.case_data #>> '{farm,tenant_id}', ''),
    'legacy:default'
)
WHERE tenant_id IS NULL OR btrim(tenant_id) = '';
ALTER TABLE case_records ALTER COLUMN tenant_id SET DEFAULT 'legacy:default';
ALTER TABLE case_records ALTER COLUMN tenant_id SET NOT NULL;
ALTER TABLE case_records DROP CONSTRAINT IF EXISTS case_records_tenant_id_nonblank;
ALTER TABLE case_records ADD CONSTRAINT case_records_tenant_id_nonblank
    CHECK (btrim(tenant_id) <> '');
CREATE INDEX IF NOT EXISTS case_records_tenant_status_updated
    ON case_records (tenant_id, status, updated_at DESC, id);

-- Consent is tenant-bound in addition to actor-bound. Existing grants stay
-- in the legacy tenant until an operator explicitly migrates them.
ALTER TABLE ofi_consent_grants ADD COLUMN IF NOT EXISTS tenant_id TEXT;
UPDATE ofi_consent_grants
SET tenant_id = 'legacy:default'
WHERE tenant_id IS NULL OR btrim(tenant_id) = '';
ALTER TABLE ofi_consent_grants ALTER COLUMN tenant_id SET DEFAULT 'legacy:default';
ALTER TABLE ofi_consent_grants ALTER COLUMN tenant_id SET NOT NULL;
ALTER TABLE ofi_consent_grants DROP CONSTRAINT IF EXISTS ofi_consent_grants_tenant_id_nonblank;
ALTER TABLE ofi_consent_grants ADD CONSTRAINT ofi_consent_grants_tenant_id_nonblank
    CHECK (btrim(tenant_id) <> '');
CREATE INDEX IF NOT EXISTS ofi_consent_grants_tenant_actor_created
    ON ofi_consent_grants (tenant_id, actor_id, created_at DESC, consent_id);

-- Evidence comparison policy IDs are now namespaced by tenant. Drop dependent
-- foreign keys before replacing the existing policy keys.
ALTER TABLE evidence_comparison_assessments
    DROP CONSTRAINT IF EXISTS evidence_comparison_assessments_policy_id_policy_version_fkey;
ALTER TABLE evidence_comparison_assessments
    DROP CONSTRAINT IF EXISTS evidence_comparison_assessments_tenant_policy_fkey;
ALTER TABLE evidence_comparison_policy_versions
    DROP CONSTRAINT IF EXISTS evidence_comparison_policy_versions_policy_id_fkey;
ALTER TABLE evidence_comparison_policy_versions
    DROP CONSTRAINT IF EXISTS evidence_comparison_policy_versions_tenant_policy_fkey;

ALTER TABLE evidence_comparison_policy_heads ADD COLUMN IF NOT EXISTS tenant_id TEXT;
UPDATE evidence_comparison_policy_heads
SET tenant_id = 'legacy:default'
WHERE tenant_id IS NULL OR btrim(tenant_id) = '';
ALTER TABLE evidence_comparison_policy_heads ALTER COLUMN tenant_id SET DEFAULT 'legacy:default';
ALTER TABLE evidence_comparison_policy_heads ALTER COLUMN tenant_id SET NOT NULL;
ALTER TABLE evidence_comparison_policy_heads
    DROP CONSTRAINT IF EXISTS evidence_comparison_policy_heads_tenant_id_nonblank;
ALTER TABLE evidence_comparison_policy_heads
    ADD CONSTRAINT evidence_comparison_policy_heads_tenant_id_nonblank
    CHECK (btrim(tenant_id) <> '');
ALTER TABLE evidence_comparison_policy_heads
    DROP CONSTRAINT IF EXISTS evidence_comparison_policy_heads_pkey;
ALTER TABLE evidence_comparison_policy_heads
    ADD CONSTRAINT evidence_comparison_policy_heads_pkey PRIMARY KEY (tenant_id, policy_id);

ALTER TABLE evidence_comparison_policy_versions ADD COLUMN IF NOT EXISTS tenant_id TEXT;
UPDATE evidence_comparison_policy_versions AS v
SET tenant_id = h.tenant_id
FROM evidence_comparison_policy_heads AS h
WHERE h.policy_id = v.policy_id
  AND (v.tenant_id IS NULL OR btrim(v.tenant_id) = '');
UPDATE evidence_comparison_policy_versions
SET tenant_id = 'legacy:default'
WHERE tenant_id IS NULL OR btrim(tenant_id) = '';
ALTER TABLE evidence_comparison_policy_versions ALTER COLUMN tenant_id SET DEFAULT 'legacy:default';
ALTER TABLE evidence_comparison_policy_versions ALTER COLUMN tenant_id SET NOT NULL;
ALTER TABLE evidence_comparison_policy_versions
    DROP CONSTRAINT IF EXISTS evidence_comparison_policy_versions_tenant_id_nonblank;
ALTER TABLE evidence_comparison_policy_versions
    ADD CONSTRAINT evidence_comparison_policy_versions_tenant_id_nonblank
    CHECK (btrim(tenant_id) <> '');
ALTER TABLE evidence_comparison_policy_versions
    DROP CONSTRAINT IF EXISTS evidence_comparison_policy_versions_pkey;
ALTER TABLE evidence_comparison_policy_versions
    ADD CONSTRAINT evidence_comparison_policy_versions_pkey
    PRIMARY KEY (tenant_id, policy_id, version);
ALTER TABLE evidence_comparison_policy_versions
    ADD CONSTRAINT evidence_comparison_policy_versions_tenant_policy_fkey
    FOREIGN KEY (tenant_id, policy_id)
    REFERENCES evidence_comparison_policy_heads (tenant_id, policy_id)
    ON DELETE RESTRICT;

ALTER TABLE evidence_comparison_assessments ADD COLUMN IF NOT EXISTS tenant_id TEXT;
UPDATE evidence_comparison_assessments AS a
SET tenant_id = v.tenant_id
FROM evidence_comparison_policy_versions AS v
WHERE v.policy_id = a.policy_id
  AND v.version = a.policy_version
  AND (a.tenant_id IS NULL OR btrim(a.tenant_id) = '');
UPDATE evidence_comparison_assessments
SET tenant_id = 'legacy:default'
WHERE tenant_id IS NULL OR btrim(tenant_id) = '';
ALTER TABLE evidence_comparison_assessments ALTER COLUMN tenant_id SET DEFAULT 'legacy:default';
ALTER TABLE evidence_comparison_assessments ALTER COLUMN tenant_id SET NOT NULL;
ALTER TABLE evidence_comparison_assessments
    DROP CONSTRAINT IF EXISTS evidence_comparison_assessments_tenant_id_nonblank;
ALTER TABLE evidence_comparison_assessments
    ADD CONSTRAINT evidence_comparison_assessments_tenant_id_nonblank
    CHECK (btrim(tenant_id) <> '');
ALTER TABLE evidence_comparison_assessments
    ADD CONSTRAINT evidence_comparison_assessments_tenant_policy_fkey
    FOREIGN KEY (tenant_id, policy_id, policy_version)
    REFERENCES evidence_comparison_policy_versions (tenant_id, policy_id, version)
    ON DELETE RESTRICT;
CREATE INDEX IF NOT EXISTS evidence_comparison_assessments_tenant_farm_time
    ON evidence_comparison_assessments (tenant_id, farm_id, as_of DESC, assessment_id);
CREATE INDEX IF NOT EXISTS evidence_comparison_policy_heads_tenant_current
    ON evidence_comparison_policy_heads (tenant_id, current_version DESC, policy_id);

-- Prevent mutable repositories from silently moving existing resources to a
-- different tenant. Administrative reassignments require an explicit audited
-- migration, not an ordinary application UPDATE.
CREATE OR REPLACE FUNCTION guard_ofi_tenant_binding_mutation()
RETURNS TRIGGER AS $$
BEGIN
    IF NEW.tenant_id IS DISTINCT FROM OLD.tenant_id THEN
        RAISE EXCEPTION 'tenant binding is immutable';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS farms_tenant_immutable_trigger ON farms;
CREATE TRIGGER farms_tenant_immutable_trigger
BEFORE UPDATE OF tenant_id ON farms
FOR EACH ROW EXECUTE FUNCTION guard_ofi_tenant_binding_mutation();

DROP TRIGGER IF EXISTS case_records_tenant_immutable_trigger ON case_records;
CREATE TRIGGER case_records_tenant_immutable_trigger
BEFORE UPDATE OF tenant_id ON case_records
FOR EACH ROW EXECUTE FUNCTION guard_ofi_tenant_binding_mutation();

DROP TRIGGER IF EXISTS evidence_comparison_policy_heads_tenant_immutable_trigger
    ON evidence_comparison_policy_heads;
CREATE TRIGGER evidence_comparison_policy_heads_tenant_immutable_trigger
BEFORE UPDATE OF tenant_id ON evidence_comparison_policy_heads
FOR EACH ROW EXECUTE FUNCTION guard_ofi_tenant_binding_mutation();

-- The v3 consent immutability trigger must also treat tenant identity as an
-- immutable term of the original grant.
DROP TRIGGER IF EXISTS ofi_consent_grant_immutable_trigger ON ofi_consent_grants;
CREATE TRIGGER ofi_consent_grant_immutable_trigger
BEFORE UPDATE OR DELETE ON ofi_consent_grants
FOR EACH ROW EXECUTE FUNCTION guard_ofi_consent_grant_mutation();

DROP TRIGGER IF EXISTS evidence_comparison_policy_versions_immutable_trigger
    ON evidence_comparison_policy_versions;
CREATE TRIGGER evidence_comparison_policy_versions_immutable_trigger
BEFORE UPDATE OR DELETE ON evidence_comparison_policy_versions
FOR EACH ROW EXECUTE FUNCTION reject_evidence_comparison_record_mutation();

DROP TRIGGER IF EXISTS evidence_comparison_assessments_immutable_trigger
    ON evidence_comparison_assessments;
CREATE TRIGGER evidence_comparison_assessments_immutable_trigger
BEFORE UPDATE OR DELETE ON evidence_comparison_assessments
FOR EACH ROW EXECUTE FUNCTION reject_evidence_comparison_record_mutation();

CREATE OR REPLACE FUNCTION guard_ofi_consent_grant_mutation()
RETURNS TRIGGER AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'consent grant records cannot be deleted';
    END IF;

    IF NEW.consent_id IS DISTINCT FROM OLD.consent_id
       OR NEW.tenant_id IS DISTINCT FROM OLD.tenant_id
       OR NEW.actor_id IS DISTINCT FROM OLD.actor_id
       OR NEW.purpose IS DISTINCT FROM OLD.purpose
       OR NEW.status IS DISTINCT FROM OLD.status
       OR NEW.scopes IS DISTINCT FROM OLD.scopes
       OR NEW.granted_at IS DISTINCT FROM OLD.granted_at
       OR NEW.expires_at IS DISTINCT FROM OLD.expires_at
       OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
        RAISE EXCEPTION 'consent grant terms are immutable';
    END IF;

    IF OLD.revoked_at IS NOT NULL
       AND NEW.revoked_at IS DISTINCT FROM OLD.revoked_at THEN
        RAISE EXCEPTION 'consent revocation timestamp cannot be changed';
    END IF;

    IF OLD.revoked_at IS NULL AND NEW.revoked_at IS NULL THEN
        RAISE EXCEPTION 'consent grant updates may only revoke the grant';
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
