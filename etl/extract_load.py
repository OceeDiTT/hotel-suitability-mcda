"""Stage 1 - EXTRACT source layers, validate CRS, LOAD into PostGIS schema `raw`."""
import logging
import geopandas as gpd
import pandas as pd
from pyproj import CRS
from sqlalchemy import text

log = logging.getLogger("etl.load")
NAME_CANDIDATES = ["freguesia", "nome", "name", "parish", "dicofre", "designacao"]


def read_validated(path, crs):
    gdf = gpd.read_file(path)
    target = CRS.from_user_input(crs)
    if gdf.crs is None:
        gdf = gdf.set_crs(target)
    elif CRS.from_user_input(gdf.crs) != target:
        gdf = gdf.to_crs(target)
    gdf.columns = [c.lower() if c != gdf.geometry.name else c for c in gdf.columns]
    return gdf


def _ensure_gist(engine, table):
    with engine.begin() as c:
        has = c.execute(text(
            "SELECT 1 FROM pg_indexes WHERE schemaname='raw' AND tablename=:t "
            "AND indexdef ILIKE '%gist%'"), {"t": table}).first()
        if not has:
            c.execute(text(f'CREATE INDEX "raw_{table}_gix" ON raw."{table}" USING GIST (geometry)'))


def _parish_name_column(gdf):
    cols = [c for c in gdf.columns if c != gdf.geometry.name]
    for cand in NAME_CANDIDATES:
        for c in cols:
            if c == cand and pd.api.types.is_string_dtype(gdf[c]):
                return c
    for c in cols:                       # fallback: first text column
        if pd.api.types.is_string_dtype(gdf[c]):
            return c
    return None


def load_raw(engine, p, paths):
    """Returns {table: {rows, file}} lineage info."""
    lineage = {}
    parishes = read_validated(paths.parishes, p.crs)
    col = _parish_name_column(parishes)
    parishes["parish_name"] = parishes[col].astype(str) if col else parishes.index.astype(str)
    log.info("parish name column: %s", col)
    study = parishes.dissolve()

    layers = {
        "parishes": parishes,
        "landcover": read_validated(paths.landcover, p.crs),
        "urban_plan": read_validated(paths.urban, p.crs),
    }
    # Roads (~870k features statewide): read only what intersects the study area + 500 m.
    roads = gpd.read_file(paths.roads, bbox=study.buffer(500))
    if roads.crs is None:
        roads = roads.set_crs(p.crs)
    roads = roads.to_crs(p.crs)
    roads.columns = [c.lower() if c != roads.geometry.name else c for c in roads.columns]
    keep = [c for c in ("osm_id", "code", "fclass", "name", roads.geometry.name) if c in roads.columns]
    layers["roads"] = roads[keep]

    src = {"parishes": paths.parishes, "landcover": paths.landcover,
           "urban_plan": paths.urban, "roads": paths.roads}
    for name, gdf in layers.items():
        if gdf.geometry.name != "geometry":
            gdf = gdf.rename_geometry("geometry")
        gdf.to_postgis(name, engine, schema="raw", if_exists="replace",
                       index=True, index_label="fid", chunksize=20000)
        _ensure_gist(engine, name)
        lineage[name] = {"rows": int(len(gdf)), "file": str(src[name]), "crs": p.crs}
        log.info("raw.%s: %d rows", name, len(gdf))
    return lineage
