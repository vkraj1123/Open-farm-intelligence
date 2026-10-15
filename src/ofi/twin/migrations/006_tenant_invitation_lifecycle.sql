-- Version 6: tenant invitation lifecycle; raw tokens are never persisted.
CREATE TABLE IF NOT EXISTS ofi_tenant_invitations (
    invitation_id TEXT PRIMARY KEY CHECK (btrim(invitation_id) <> ''),
    tenant_id TEXT NOT NULL CHECK (btrim(tenant_id) <> ''),
    invited_actor_id TEXT NOT NULL CHECK (btrim(invited_actor_id) <> ''),
    token_sha256 TEXT NOT NULL UNIQUE CHECK (token_sha256 ~ '^[0-9a-f]{64}$'),
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'redeemed', 'revoked', 'expired')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at TIMESTAMPTZ NOT NULL,
    created_by TEXT NOT NULL CHECK (btrim(created_by) <> ''),
    redeemed_at TIMESTAMPTZ,
    redeemed_by TEXT,
    revoked_at TIMESTAMPTZ,
    revoked_by TEXT,
    CHECK (expires_at > created_at),
    CHECK ((status = 'redeemed') = (redeemed_at IS NOT NULL)),
    CHECK ((status = 'redeemed') = (redeemed_by IS NOT NULL)),
    CHECK ((status = 'revoked') = (revoked_at IS NOT NULL)),
    CHECK ((status = 'revoked') = (revoked_by IS NOT NULL))
);
CREATE INDEX IF NOT EXISTS ofi_tenant_invitations_tenant_created
    ON ofi_tenant_invitations (tenant_id, created_at DESC);
CREATE UNIQUE INDEX IF NOT EXISTS ofi_tenant_invitations_one_pending_actor
    ON ofi_tenant_invitations (tenant_id, invited_actor_id) WHERE status = 'pending';

CREATE TABLE IF NOT EXISTS ofi_tenant_invitation_events (
    event_id BIGSERIAL PRIMARY KEY,
    invitation_id TEXT NOT NULL,
    tenant_id TEXT NOT NULL CHECK (btrim(tenant_id) <> ''),
    invited_actor_id TEXT NOT NULL CHECK (btrim(invited_actor_id) <> ''),
    action TEXT NOT NULL CHECK (action IN ('created', 'redeemed', 'revoked', 'expired')),
    status TEXT NOT NULL CHECK (status IN ('pending', 'redeemed', 'revoked', 'expired')),
    performed_by TEXT NOT NULL CHECK (btrim(performed_by) <> ''),
    occurred_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ofi_tenant_invitation_events_tenant_time
    ON ofi_tenant_invitation_events (tenant_id, event_id DESC);

CREATE OR REPLACE FUNCTION reject_ofi_tenant_invitation_event_mutation()
RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION 'tenant invitation audit events are append-only';
END;
$$ LANGUAGE plpgsql;

DO $ofi_migration$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_trigger
        WHERE tgname = 'ofi_tenant_invitation_events_immutable_trigger'
          AND tgrelid = 'ofi_tenant_invitation_events'::regclass
    ) THEN
        CREATE TRIGGER ofi_tenant_invitation_events_immutable_trigger
        BEFORE UPDATE OR DELETE ON ofi_tenant_invitation_events
        FOR EACH ROW EXECUTE FUNCTION reject_ofi_tenant_invitation_event_mutation();
    END IF;
END;
$ofi_migration$;
