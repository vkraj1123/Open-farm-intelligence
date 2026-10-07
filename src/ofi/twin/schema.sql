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
