-- PostgreSQL/PostGIS persistence boundary for the OFI farm twin.
-- This schema is intentionally additive: intelligence code uses the
-- FarmTwinRepository interface and does not depend on SQL details.

CREATE EXTENSION IF NOT EXISTS postgis;

CREATE TABLE IF NOT EXISTS farms (
    id TEXT PRIMARY KEY,
    farmer_id TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS parcels (
    id TEXT PRIMARY KEY,
    farm_id TEXT NOT NULL REFERENCES farms(id),
    area_ha DOUBLE PRECISION,
    location GEOGRAPHY(POINT, 4326) NOT NULL,
    boundary GEOGRAPHY(POLYGON, 4326),
    administrative_area JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS parcels_boundary_gist
    ON parcels USING GIST (boundary);

CREATE TABLE IF NOT EXISTS crop_cycles (
    id TEXT PRIMARY KEY,
    farm_id TEXT NOT NULL REFERENCES farms(id),
    crop TEXT NOT NULL,
    season TEXT,
    sowing_date DATE,
    harvest_date DATE,
    irrigation_method TEXT,
    status TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS crop_cycles_farm_dates
    ON crop_cycles (farm_id, sowing_date, harvest_date);

CREATE TABLE IF NOT EXISTS observations (
    id TEXT PRIMARY KEY,
    farm_id TEXT NOT NULL REFERENCES farms(id),
    crop_cycle_id TEXT REFERENCES crop_cycles(id),
    kind TEXT NOT NULL,
    observed_at TIMESTAMPTZ NOT NULL,
    location GEOGRAPHY(POINT, 4326),
    spatial_scope TEXT,
    unit TEXT,
    source TEXT NOT NULL,
    quality DOUBLE PRECISION NOT NULL,
    confidence DOUBLE PRECISION NOT NULL,
    value JSONB NOT NULL,
    provenance JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS observations_farm_time
    ON observations (farm_id, observed_at DESC);

CREATE INDEX IF NOT EXISTS observations_location_gist
    ON observations USING GIST (location);

CREATE INDEX IF NOT EXISTS observations_value_gin
    ON observations USING GIN (value);

CREATE TABLE IF NOT EXISTS land_parties (
    farm_id TEXT NOT NULL REFERENCES farms(id),
    party_id TEXT NOT NULL,
    role TEXT NOT NULL,
    valid_from DATE NOT NULL,
    valid_to DATE,
    verification TEXT NOT NULL,
    PRIMARY KEY (farm_id, party_id, role, valid_from)
);

CREATE TABLE IF NOT EXISTS production_contracts (
    contract_id TEXT PRIMARY KEY,
    farm_id TEXT NOT NULL REFERENCES farms(id),
    owner_id TEXT NOT NULL,
    cultivator_id TEXT NOT NULL,
    valid_from DATE NOT NULL,
    valid_to DATE NOT NULL,
    arrangement TEXT
);

-- Useful parcel-scoped observation query:
-- SELECT o.*
-- FROM observations o
-- JOIN parcels p ON p.farm_id = o.farm_id
-- WHERE o.location IS NOT NULL
--   AND ST_Contains(p.boundary::geometry, o.location::geometry);


-- Case state and append-only audit ledger.
-- The JSON snapshot keeps this boundary storage-oriented while domain models
-- remain independent of PostgreSQL. State + event are committed together.
CREATE TABLE IF NOT EXISTS case_records (
    id TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    version BIGINT NOT NULL DEFAULT 0,
    case_data JSONB NOT NULL,
    latest_reasoning JSONB,
    outcome JSONB
);

CREATE INDEX IF NOT EXISTS case_records_status_updated
    ON case_records (status, updated_at DESC);

CREATE TABLE IF NOT EXISTS case_events (
    id TEXT NOT NULL,
    case_id TEXT NOT NULL REFERENCES case_records(id) ON DELETE CASCADE,
    sequence BIGINT NOT NULL,
    event_type TEXT NOT NULL,
    occurred_at TIMESTAMPTZ NOT NULL,
    actor TEXT NOT NULL,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    PRIMARY KEY (case_id, id),
    UNIQUE (case_id, sequence)
);

CREATE INDEX IF NOT EXISTS case_events_case_sequence
    ON case_events (case_id, sequence);

CREATE INDEX IF NOT EXISTS case_events_case_time
    ON case_events (case_id, occurred_at, id);

CREATE INDEX IF NOT EXISTS case_events_type_time
    ON case_events (event_type, occurred_at DESC);


-- Durable service execution state and append-only provider status history.
-- Idempotency is enforced at the database boundary so concurrent workers
-- cannot create two transactions for the same execution key.
CREATE TABLE IF NOT EXISTS service_transactions (
    transaction_id TEXT PRIMARY KEY,
    idempotency_key TEXT NOT NULL UNIQUE,
    request_fingerprint TEXT NOT NULL,
    action_id TEXT NOT NULL,
    provider_id TEXT,
    created_at TIMESTAMPTZ NOT NULL,
    status TEXT NOT NULL,
    external_reference TEXT
);

CREATE INDEX IF NOT EXISTS service_transactions_action_created
    ON service_transactions (action_id, created_at DESC, transaction_id);

CREATE TABLE IF NOT EXISTS service_transaction_events (
    transaction_id TEXT NOT NULL REFERENCES service_transactions(transaction_id) ON DELETE CASCADE,
    sequence BIGINT NOT NULL,
    status TEXT NOT NULL,
    occurred_at TIMESTAMPTZ NOT NULL,
    external_reference TEXT,
    message TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (transaction_id, sequence)
);

CREATE INDEX IF NOT EXISTS service_transaction_events_time
    ON service_transaction_events (transaction_id, occurred_at, sequence);


-- Provider callback receipt ledger.
-- The provider/event pair is the exactly-once boundary for webhook delivery.
CREATE TABLE IF NOT EXISTS service_transaction_callbacks (
    provider_id TEXT NOT NULL,
    event_id TEXT NOT NULL,
    transaction_id TEXT NOT NULL REFERENCES service_transactions(transaction_id) ON DELETE CASCADE,
    received_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (provider_id, event_id)
);

CREATE INDEX IF NOT EXISTS service_transaction_callbacks_transaction
    ON service_transaction_callbacks (transaction_id);


-- Explicit external execution attempts. One transaction may have multiple
-- attempts when a provider fails, times out, or remains externally unknown.
CREATE TABLE IF NOT EXISTS service_execution_attempts (
    attempt_id TEXT PRIMARY KEY,
    transaction_id TEXT NOT NULL REFERENCES service_transactions(transaction_id) ON DELETE CASCADE,
    attempt_number BIGINT NOT NULL,
    provider_id TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    external_reference TEXT,
    last_error TEXT,
    UNIQUE (transaction_id, attempt_number)
);

CREATE INDEX IF NOT EXISTS service_execution_attempts_transaction
    ON service_execution_attempts (transaction_id, attempt_number);
