"""Read-only JSON/GeoJSON API over the `analysis` schema + static dashboard."""
import os
import re
from contextlib import asynccontextmanager
from pathlib import Path

import psycopg
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

DB_URL = re.sub(r"^postgresql\+psycopg://", "postgresql://",
                os.environ.get("DATABASE_URL", "postgresql://hotel:hotel@localhost:5432/hotel"))
pool = ConnectionPool(DB_URL, min_size=1, max_size=5, open=False, kwargs={"row_factory": dict_row})


@asynccontextmanager
async def lifespan(app):
    pool.open()
    yield
    pool.close()


app = FastAPI(title="Lisbon hotel site suitability", lifespan=lifespan)


@app.exception_handler(psycopg.errors.UndefinedTable)
async def _not_initialised(request: Request, exc):
    return JSONResponse({"detail": "Database not initialised - run the ETL first."}, status_code=503)


def q(sql, params=()):
    with pool.connection() as conn:
        return conn.execute(sql, params).fetchall()


def resolve_run(run_id):
    if run_id is None:
        rows = q("SELECT run_id FROM analysis.run ORDER BY run_id DESC LIMIT 1")
        if not rows:
            raise HTTPException(404, "No ETL run found - run the ETL first.")
        return rows[0]["run_id"]
    if not q("SELECT 1 FROM analysis.run WHERE run_id=%s", (run_id,)):
        raise HTTPException(404, f"run {run_id} not found")
    return run_id


@app.get("/api/health")
def health():
    q("SELECT 1")
    return {"status": "ok"}


@app.get("/api/runs")
def runs():
    return q("SELECT run_id, created_at, label, n_candidates, n_final FROM analysis.run ORDER BY run_id DESC")


@app.get("/api/summary")
def summary(run_id: int | None = None):
    rid = resolve_run(run_id)
    run = q("SELECT run_id, created_at, label, params, sources, qa, n_candidates, n_final "
            "FROM analysis.run WHERE run_id=%s", (rid,))[0]
    run["parishes"] = q("SELECT parish_name, n_sites, total_ha, largest_ha FROM analysis.v_parish_summary "
                        "WHERE run_id=%s ORDER BY total_ha DESC", (rid,))
    run["total_final_ha"] = float(sum(r["total_ha"] for r in run["parishes"]))
    return run


@app.get("/api/sites")
def sites(run_id: int | None = None, stage: str = Query("all", pattern="^(all|final|rejected)$")):
    rid = resolve_run(run_id)
    where = {"all": "TRUE", "final": "in_urban_plan", "rejected": "NOT in_urban_plan"}[stage]
    rows = q(f"""
        SELECT jsonb_build_object('type','FeatureCollection','features', COALESCE(jsonb_agg(
          jsonb_build_object('type','Feature','id',site_id,
            'geometry', public.ST_AsGeoJSON(public.ST_Transform(geom,4326), 6)::jsonb,
            'properties', jsonb_build_object('site_id',site_id,'area_ha',area_ha,'area_m2',area_m2,
               'rank',rank_by_area,'parish',parish_name,'score_mean',score_mean,'score_max',score_max,
               'in_urban_plan',in_urban_plan)) ORDER BY area_m2 DESC), '[]'::jsonb)) AS fc
        FROM analysis.v_site_ranked WHERE run_id=%s AND {where}""", (rid,))
    return rows[0]["fc"]


def _layer(table, props_sql, simplify):
    rows = q(f"""
        SELECT jsonb_build_object('type','FeatureCollection','features', COALESCE(jsonb_agg(
          jsonb_build_object('type','Feature',
            'geometry', public.ST_AsGeoJSON(public.ST_Transform(public.ST_SimplifyPreserveTopology(geometry,{simplify}),4326), 6)::jsonb,
            'properties', {props_sql})), '[]'::jsonb)) AS fc FROM raw.{table}""")
    return rows[0]["fc"]


@app.get("/api/layers/parishes")
def parishes():
    return _layer("parishes", "jsonb_build_object('name', parish_name)", 5)


@app.get("/api/layers/urban-plan")
def urban_plan():
    return _layer("urban_plan", "jsonb_build_object('fid', fid)", 5)


@app.get("/api/overlays")
def overlays(run_id: int | None = None):
    rid = resolve_run(run_id)
    return {"run_id": rid, "overlays": q(
        "SELECT name, title, description, south, west, north, east FROM analysis.overlay "
        "WHERE run_id=%s ORDER BY name", (rid,))}


@app.get("/api/overlays/{name}.png")
def overlay_png(name: str, run_id: int | None = None):
    rid = resolve_run(run_id)
    rows = q("SELECT png FROM analysis.overlay WHERE run_id=%s AND name=%s", (rid, name))
    if not rows:
        raise HTTPException(404, "overlay not found")
    return Response(bytes(rows[0]["png"]), media_type="image/png",
                    headers={"Cache-Control": "public, max-age=3600"})


WEB = Path(__file__).resolve().parent.parent / "web"
app.mount("/", StaticFiles(directory=WEB, html=True), name="web")
