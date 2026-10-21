# Lisbon hotel site suitability: database, ETL and live dashboard

Productionises `suitability_analysis_experiment.ipynb` (MCDA: roads ≥ 20 m, land cover, slope ≤ 10 %,
≥ 1 ha, inside the urbanisation plan → 125 candidates → 19 sites).

```
data/*.shp,*.tif ──► ETL (python -m etl.run) ──► PostgreSQL/PostGIS ──► FastAPI ──► Leaflet dashboard
   Stage 1 extract+validate CRS → raw.*          raw.*  (parishes, roads, landcover, urban_plan)
   Stage 2 MCDA (notebook logic)                 analysis.run / site / overlay (+ 2 views)
   Stage 3 publish sites + PNG overlays          /api/*  +  static web/index.html
```

## Run locally
```bash
# put the notebook's data/ folder next to this file (same layout: data/Lx_Streets/..., data/Lx_elevation/...)
docker compose up -d db api
docker compose run --rm etl            # loads raw layers, runs the model, publishes results
open http://localhost:8000
```
Parameter experiments (each creates a new, selectable run in the dashboard):
`docker compose run --rm etl --skip-raw --label "30 m buffer" --set road_buff=30 slope_max=8`

## Get a live URL
1. Push this folder to GitHub → Render → *New → Blueprint* (`render.yaml` creates Postgres + the web service).
2. Run the ETL from your machine against the hosted DB (use the database's *external* connection string):
   `DATABASE_URL=postgres://… pip install -r requirements-etl.txt && python -m etl.run --data-dir data`
3. Open the service URL. Any container host works (Fly, Railway, Cloud Run + Cloud SQL/Neon with PostGIS):
   only `DATABASE_URL` is required. Tighten the DB `ipAllowList` after the ETL has run.

## Design decisions
* **Vector layers live in PostGIS** (`raw`, EPSG:3763, GiST indexed). Road buffering/clipping (872k features) runs in SQL.
  Roads are pre-filtered to the study-area bbox + 500 m on load.
* **The DEM stays a file** (read by the ETL); its path, size and mtime are recorded in `analysis.run.sources`.
* **Results are versioned by run** with parameters, lineage and QA counts, so scenarios can be compared.
* **Raster criteria reach the browser as pre-rendered PNG overlays** (stored in `analysis.overlay`), not 430k vector cells.
* Dashboard toggles: sites, rejected candidates, urban-plan limit, parishes, one criterion raster at a time with opacity,
  basemap, minimum-area filter, run selector. Panel collapses on mobile.

## Things the notebook does that you should know about
* **Every final site has the same weighted score (4.25).** Sites are cells with all three criteria = 1, so the weighted
  sum cannot rank them; `score_mean/score_max` are stored for fidelity but sites are **ranked by area**. If you want real
  ranking, the weighted raster needs to feed the selection (e.g. a score threshold instead of sum == 3) or new criteria
  (transit, attractions) as the notebook's conclusion suggests.
* **Cells without DEM slope are treated as unsuitable** (46,569 in the notebook). The ETL keeps that default for
  reproducibility, shows the share as a warning in the dashboard, and exposes `--set missing_slope_suitable=true`.
* The parish name column is auto-detected (`freguesia`, `nome`, `name`, …, else first text column); check `raw.parishes.parish_name`.
* A site is assigned to the parish containing a point on its surface (a site may straddle parishes).

## Status of this code
Verified in the build sandbox: unit tests for the numpy raster maths (`tests/`, run with pytest), Python and JS syntax.
**Not run end-to-end**: the sandbox had no source data, PostgreSQL/GDAL or network. Expect to confirm on first run that you
get 125 candidates / 19 sites; small differences could come from the SQL road clip/buffer (`quad_segs=16` matches shapely's default) and the
bbox pre-filter. Check the Render plan names and PostGIS support against Render's docs.
