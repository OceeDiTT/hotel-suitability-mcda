-- Lisbon hotel site suitability: database schema (PostgreSQL + PostGIS)
-- Idempotent: safe to run repeatedly (docker-entrypoint-initdb.d and the ETL both run it).
CREATE EXTENSION IF NOT EXISTS postgis;
CREATE SCHEMA IF NOT EXISTS raw;       -- source layers, loaded as-is (EPSG:3763)
CREATE SCHEMA IF NOT EXISTS analysis;  -- model outputs, versioned by run

-- One row per ETL execution. Parameters are stored so runs can be compared.
CREATE TABLE IF NOT EXISTS analysis.run (
    run_id        serial PRIMARY KEY,
    created_at    timestamptz NOT NULL DEFAULT now(),
    label         text,
    params        jsonb NOT NULL,   -- cell size, buffer, slope max, weights ...
    sources       jsonb,            -- lineage: input files, sizes, row counts
    qa            jsonb,            -- cell counts, criteria pass rates, missing slope cells
    n_candidates  integer,          -- contiguous areas >= min_area meeting all 3 raster criteria
    n_final       integer           -- ... that also lie fully inside the urbanisation plan
);

-- Candidate areas (>= min_area). in_urban_plan = TRUE marks the final hotel sites.
CREATE TABLE IF NOT EXISTS analysis.site (
    site_id       serial PRIMARY KEY,
    run_id        integer NOT NULL REFERENCES analysis.run(run_id) ON DELETE CASCADE,
    geom          geometry(Polygon, 3763) NOT NULL,
    area_m2       double precision NOT NULL,
    score_mean    double precision,
    score_max     double precision,
    in_urban_plan boolean NOT NULL,
    parish_name   text
);
CREATE INDEX IF NOT EXISTS site_geom_gix ON analysis.site USING GIST (geom);
CREATE INDEX IF NOT EXISTS site_run_ix   ON analysis.site (run_id, in_urban_plan);

-- Pre-rendered Web-Mercator PNG overlays of the criteria rasters (for the dashboard).
CREATE TABLE IF NOT EXISTS analysis.overlay (
    run_id      integer NOT NULL REFERENCES analysis.run(run_id) ON DELETE CASCADE,
    name        text NOT NULL,
    title       text NOT NULL,
    description text,
    png         bytea NOT NULL,
    south double precision NOT NULL, west double precision NOT NULL,
    north double precision NOT NULL, east double precision NOT NULL,
    PRIMARY KEY (run_id, name)
);
