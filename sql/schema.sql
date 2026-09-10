-- ian-relief-logistics: PostGIS schema
--
-- Conventions
--   * Every geometry column is EPSG:4326 (WGS84 lon/lat). Distances in the demo are computed with
--     ST_Distance on geography casts (metres), never on raw degrees.
--   * Every table has a stable primary key so loaders can UPSERT (INSERT ... ON CONFLICT) and be
--     re-run safely. Re-running a loader must not create duplicates.
--   * All timestamps are TIMESTAMPTZ and stored in UTC. Local Florida time (EDT, UTC-4 in Sept 2022)
--     is a display concern only.
--   * source_system / source_id keep the link back to the public dataset a row came from.
--
-- This file is applied automatically by docker-compose on first boot, and can be re-applied with
-- `make schema` (everything is IF NOT EXISTS / OR REPLACE).

CREATE EXTENSION IF NOT EXISTS postgis;

-- ---------------------------------------------------------------------------------------------
-- sites: shelters AND warehouses share one table so "nearest site of type X" is one query.
-- site_id is OURS (e.g. SH-134070, WH-01). The original id lives in source_id and in site_crosswalk.
-- ---------------------------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS sites (
    site_id              TEXT PRIMARY KEY,
    site_type            TEXT NOT NULL CHECK (site_type IN ('shelter', 'warehouse', 'pod', 'other')),
    name                 TEXT NOT NULL,
    address              TEXT,
    city                 TEXT,
    county               TEXT,
    county_fips          TEXT,
    state                TEXT DEFAULT 'FL',
    source_system        TEXT NOT NULL,           -- 'HIFLD_NSS' | 'SYNTHETIC' | ...
    source_id            TEXT,                    -- id in the source system (HIFLD shelter_id, etc.)
    evacuation_capacity  INTEGER,
    post_impact_capacity INTEGER,
    pet_friendly         BOOLEAN,
    generator_onsite     BOOLEAN,
    in_surge_zone        BOOLEAN,
    attributes           JSONB DEFAULT '{}'::jsonb,   -- everything else from the source, unmodified
    geom                 geometry(Point, 4326) NOT NULL,
    loaded_at            TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS sites_geom_gix ON sites USING GIST (geom);
CREATE INDEX IF NOT EXISTS sites_type_idx ON sites (site_type);

-- One source row can map to one of our sites; one of our sites can have several source ids
-- (e.g. HIFLD shelter_id AND a Red Cross facility code). This is the crosswalk.
CREATE TABLE IF NOT EXISTS site_crosswalk (
    site_id       TEXT NOT NULL REFERENCES sites(site_id) ON DELETE CASCADE,
    source_system TEXT NOT NULL,
    source_id     TEXT NOT NULL,
    PRIMARY KEY (source_system, source_id)
);

-- ---------------------------------------------------------------------------------------------
-- roads: OSM ways from Overpass. One row per way. Geometry is the way's LineString.
-- Kingsley: routing (pgRouting / OSRM) would need a topology built from these; not done here.
-- ---------------------------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS roads (
    osm_id       BIGINT PRIMARY KEY,
    highway      TEXT NOT NULL,                 -- motorway, trunk, primary, ...
    name         TEXT,
    ref          TEXT,                          -- 'I 75', 'US 41', 'FL 78' ...
    oneway       BOOLEAN,
    lanes        SMALLINT,
    maxspeed     TEXT,                          -- raw OSM string ('45 mph'); parse later if needed
    bridge       BOOLEAN,
    tags         JSONB DEFAULT '{}'::jsonb,
    geom         geometry(LineString, 4326) NOT NULL,
    length_m     DOUBLE PRECISION,                -- filled by the loader: ST_Length(geom::geography)
    loaded_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS roads_geom_gix ON roads USING GIST (geom);
CREATE INDEX IF NOT EXISTS roads_highway_idx ON roads (highway);

-- ---------------------------------------------------------------------------------------------
-- closures: road/bridge closures as time windows. Reconstructed from public reporting; each row
-- carries its source_note and a time_confidence so nobody mistakes these for official logs.
-- end_utc NULL = still closed at the end of the scenario horizon.
-- ---------------------------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS closures (
    closure_id       TEXT PRIMARY KEY,
    name             TEXT NOT NULL,
    closure_type     TEXT NOT NULL CHECK (closure_type IN ('bridge_collapse', 'washout', 'flooding', 'debris', 'inspection', 'other')),
    start_utc        TIMESTAMPTZ NOT NULL,
    end_utc          TIMESTAMPTZ,
    time_confidence  TEXT NOT NULL CHECK (time_confidence IN ('reported', 'approximate', 'assumed')),
    osm_ref          TEXT,                       -- road ref the closure affects ('FL 867', 'I 75')
    source_note      TEXT NOT NULL,
    geom             geometry(Point, 4326) NOT NULL,   -- representative point (bridge centre / segment midpoint)
    loaded_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (end_utc IS NULL OR end_utc > start_utc)
);
CREATE INDEX IF NOT EXISTS closures_geom_gix ON closures USING GIST (geom);
CREATE INDEX IF NOT EXISTS closures_window_idx ON closures (start_utc, end_utc);

-- ---------------------------------------------------------------------------------------------
-- vehicles: the truck fleet. Synthetic. home_site_id points at a warehouse.
-- ---------------------------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS vehicles (
    vehicle_id        TEXT PRIMARY KEY,
    vehicle_type      TEXT NOT NULL,             -- box_truck, flatbed, reefer, ...
    capacity_kg       INTEGER NOT NULL CHECK (capacity_kg > 0),
    capacity_pallets  SMALLINT,
    home_site_id      TEXT REFERENCES sites(site_id),
    available_from_utc TIMESTAMPTZ,
    notes             TEXT,
    loaded_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------------------------------
-- inventory: what each warehouse holds of each commodity at a moment in time (a snapshot series).
-- ---------------------------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS inventory (
    site_id       TEXT NOT NULL REFERENCES sites(site_id),
    commodity     TEXT NOT NULL,                 -- water_cases, meals, tarps, ...
    as_of_utc     TIMESTAMPTZ NOT NULL,
    qty_on_hand   INTEGER NOT NULL CHECK (qty_on_hand >= 0),
    unit_kg       NUMERIC(8,2),                  -- weight per unit, for truck capacity maths
    loaded_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (site_id, commodity, as_of_utc)
);

-- ---------------------------------------------------------------------------------------------
-- demand: what each shelter needs of each commodity, valid from a moment in time, and how much
-- has been delivered so far. unmet = qty_needed - qty_delivered.
-- ---------------------------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS demand (
    site_id        TEXT NOT NULL REFERENCES sites(site_id),
    commodity      TEXT NOT NULL,
    valid_from_utc TIMESTAMPTZ NOT NULL,
    qty_needed     INTEGER NOT NULL CHECK (qty_needed >= 0),
    qty_delivered  INTEGER NOT NULL DEFAULT 0 CHECK (qty_delivered >= 0),
    priority       SMALLINT NOT NULL DEFAULT 3 CHECK (priority BETWEEN 1 AND 5),   -- 1 = most urgent
    notes          TEXT,
    loaded_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (site_id, commodity, valid_from_utc)
);

-- ---------------------------------------------------------------------------------------------
-- storm_track: NHC best track for AL092022 (Ian). One row per 6-hourly fix (plus landfall rows).
-- ---------------------------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS storm_track (
    atcf_id       TEXT NOT NULL,
    time_utc      TIMESTAMPTZ NOT NULL,
    record_id     TEXT NOT NULL DEFAULT '',      -- 'L' = landfall, '' otherwise (part of the PK, so never NULL)
    status        TEXT,                          -- TD, TS, HU, EX, ...
    wind_kt       SMALLINT,
    pressure_mb   SMALLINT,
    r34_ne_nmi    SMALLINT, r34_se_nmi SMALLINT, r34_sw_nmi SMALLINT, r34_nw_nmi SMALLINT,
    r64_ne_nmi    SMALLINT, r64_se_nmi SMALLINT, r64_sw_nmi SMALLINT, r64_nw_nmi SMALLINT,
    geom          geometry(Point, 4326) NOT NULL,
    loaded_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (atcf_id, time_utc, record_id)
);

-- ---------------------------------------------------------------------------------------------
-- counties: TIGER boundaries for the study area (context layer for maps and spatial joins).
-- ---------------------------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS counties (
    county_fips  TEXT PRIMARY KEY,
    name         TEXT NOT NULL,
    state_fips   TEXT NOT NULL,
    geom         geometry(MultiPolygon, 4326) NOT NULL,
    loaded_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS counties_geom_gix ON counties USING GIST (geom);

-- ---------------------------------------------------------------------------------------------
-- load_log: one row per loader run so `make load` is auditable.
-- ---------------------------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS load_log (
    id            BIGSERIAL PRIMARY KEY,
    loader        TEXT NOT NULL,
    source_path   TEXT,
    rows_in       INTEGER,
    rows_upserted INTEGER,
    rows_rejected INTEGER,
    ran_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------------------------------
-- Convenience views
-- ---------------------------------------------------------------------------------------------

-- Latest demand row per (shelter, commodity) as of a given time is a parameterised query, not a
-- view; see src/ianrelief/demo.py. This view is the simple "current state" version.
CREATE OR REPLACE VIEW v_shelter_unmet_demand AS
SELECT d.site_id,
       s.name,
       s.county,
       d.commodity,
       d.valid_from_utc,
       d.qty_needed,
       d.qty_delivered,
       (d.qty_needed - d.qty_delivered) AS qty_unmet,
       d.priority,
       s.geom
FROM demand d
JOIN sites s ON s.site_id = d.site_id
WHERE s.site_type = 'shelter';

-- Closures active at a given instant: use the function, e.g.
--   SELECT * FROM closures_active_at('2022-09-29T12:00Z');
CREATE OR REPLACE FUNCTION closures_active_at(t TIMESTAMPTZ)
RETURNS SETOF closures
LANGUAGE sql STABLE AS $$
    SELECT * FROM closures
    WHERE start_utc <= t AND (end_utc IS NULL OR end_utc > t);
$$;
