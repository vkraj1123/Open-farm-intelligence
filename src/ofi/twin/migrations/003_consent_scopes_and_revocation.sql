-- Version 3: explicit scoped consents and durable, actor-bound revocation.
-- Issued grant fields are immutable; only the first revoked_at timestamp may be set.

CREATE TABLE IF NOT EXISTS ofi_consent_grants (
    consent_id TEXT PRIMARY KEY,
    actor_id TEXT NOT NULL CHECK (btrim(actor_id) <> ''),
    purpose TEXT NOT NULL CHECK (btrim(purpose) <> ''),
    status TEXT NOT NULL CHECK (status IN ('granted', 'denied', 'expired', 'not_required')),
    scopes TEXT[] NOT NULL CHECK (cardinality(scopes) >= 1),
    granted_at TIMESTAMPTZ NOT NULL,
    expires_at TIMESTAMPTZ,
    revoked_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (expires_at IS NULL OR expires_at > granted_at),
    CHECK (revoked_at IS NULL OR revoked_at >= granted_at)
);

CREATE INDEX IF NOT EXISTS ofi_consent_grants_actor_created
    ON ofi_consent_grants (actor_id, created_at DESC, consent_id);

CREATE INDEX IF NOT EXISTS ofi_consent_grants_active_actor
    ON ofi_consent_grants (actor_id, expires_at)
    WHERE status = 'granted' AND revoked_at IS NULL;

CREATE OR REPLACE FUNCTION guard_ofi_consent_grant_mutation()
RETURNS TRIGGER AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'consent grant records cannot be deleted';
    END IF;

    IF NEW.consent_id IS DISTINCT FROM OLD.consent_id
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

DROP TRIGGER IF EXISTS ofi_consent_grant_immutable_trigger
    ON ofi_consent_grants;
CREATE TRIGGER ofi_consent_grant_immutable_trigger
BEFORE UPDATE OR DELETE ON ofi_consent_grants
FOR EACH ROW EXECUTE FUNCTION guard_ofi_consent_grant_mutation();
