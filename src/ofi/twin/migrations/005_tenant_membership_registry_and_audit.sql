-- Version 5: authoritative tenant membership registry and append-only administration audit.
CREATE TABLE IF NOT EXISTS ofi_tenant_memberships (
    tenant_id TEXT NOT NULL CHECK (btrim(tenant_id) <> ''),
    actor_id TEXT NOT NULL CHECK (btrim(actor_id) <> ''),
    membership_role TEXT NOT NULL CHECK (membership_role IN ('tenant_admin', 'member')),
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'suspended', 'revoked')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_by TEXT NOT NULL CHECK (btrim(created_by) <> ''),
    updated_by TEXT NOT NULL CHECK (btrim(updated_by) <> ''),
    PRIMARY KEY (tenant_id, actor_id)
);
CREATE INDEX IF NOT EXISTS ofi_tenant_memberships_active_admins
    ON ofi_tenant_memberships (tenant_id, status, membership_role);

CREATE TABLE IF NOT EXISTS ofi_tenant_membership_events (
    event_id BIGSERIAL PRIMARY KEY,
    tenant_id TEXT NOT NULL CHECK (btrim(tenant_id) <> ''),
    subject_actor_id TEXT NOT NULL CHECK (btrim(subject_actor_id) <> ''),
    action TEXT NOT NULL CHECK (action IN ('created', 'reactivated', 'role_changed', 'suspended', 'revoked')),
    membership_role TEXT NOT NULL CHECK (membership_role IN ('tenant_admin', 'member')),
    status TEXT NOT NULL CHECK (status IN ('active', 'suspended', 'revoked')),
    performed_by TEXT NOT NULL CHECK (btrim(performed_by) <> ''),
    occurred_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ofi_tenant_membership_events_tenant_time
    ON ofi_tenant_membership_events (tenant_id, event_id DESC);

CREATE OR REPLACE FUNCTION reject_ofi_tenant_membership_event_mutation()
RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION 'tenant membership audit events are append-only';
END;
$$ LANGUAGE plpgsql;

DO $ofi_migration$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_trigger
        WHERE tgname = 'ofi_tenant_membership_events_immutable_trigger'
          AND tgrelid = 'ofi_tenant_membership_events'::regclass
    ) THEN
        CREATE TRIGGER ofi_tenant_membership_events_immutable_trigger
        BEFORE UPDATE OR DELETE ON ofi_tenant_membership_events
        FOR EACH ROW EXECUTE FUNCTION reject_ofi_tenant_membership_event_mutation();
    END IF;
END;
$ofi_migration$;
