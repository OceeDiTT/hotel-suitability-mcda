"""Stage 2 - TRANSFORM: the notebook's MCDA, reading from PostGIS `raw`.

Criteria (all rasterised on one 20 m grid snapped to the parishes' extent):
  1. roads     : >= road_buff m from any street         -> 1 / 0
  2. land cover: level-1 class in (2 agriculture, 3 forest/natural) -> 1 / 0
  3. slope     : <= slope_max % (Horn, bilinear resample) -> 1 / 0
  4. weighted sum (roads*w1 + landcover*w2 + slope*w3) - informational
  5. fully-suitable cells (sum == 3) -> polygons >= min_area -> flag 'inside urban plan'
"""
import logging
from dataclasses import dataclass
import numpy as np
import geopandas as gpd
import rasterio
from rasterio import features
from rasterio.enums import Resampling
from rasterio.transform import from_origin
from rasterio.warp import reproject
from rasterstats import zonal_stats
from shapely.geometry import shape
from sqlalchemy import text

from .raster_ops import NODATA, horn_slope_pct, reclass_slope, combine

log = logging.getLogger("etl.transform")

ROADS_SQL = text("""
WITH s AS MATERIALIZED (SELECT ST_Union(geometry) AS g FROM raw.parishes)
SELECT ST_Buffer(ST_CollectionExtract(ST_Intersection(r.geometry, s.g), 2),
                 :buff, 'quad_segs=16') AS geometry
FROM raw.roads r JOIN s ON r.geometry && s.g AND ST_Intersects(r.geometry, s.g)
""")


@dataclass
class Result:
    candidates: gpd.GeoDataFrame
    grids: dict          # name -> 2D array (reclass_* and weighted), NODATA outside study
    transform: object
    qa: dict


def _grid(study, p):
    cs = p.cell_size
    minx, miny, maxx, maxy = study.total_bounds
    minx, miny = np.floor(minx / cs) * cs, np.floor(miny / cs) * cs
    maxx, maxy = np.ceil(maxx / cs) * cs, np.ceil(maxy / cs) * cs
    n_cols, n_rows = int((maxx - minx) / cs), int((maxy - miny) / cs)
    transform = from_origin(minx, maxy, cs, cs)
    mask = features.rasterize([(g, 1) for g in study.geometry], out_shape=(n_rows, n_cols),
                              transform=transform, fill=0, dtype="uint8").astype(bool)
    log.info("grid: %d rows x %d cols @ %d m", n_rows, n_cols, cs)
    return transform, (n_rows, n_cols), mask


def run_mcda(engine, p, paths) -> Result:
    parishes = gpd.read_postgis("SELECT * FROM raw.parishes", engine, geom_col="geometry")
    study = parishes.dissolve()
    transform, shp, mask = _grid(study, p)

    # --- Criterion 1: roads (buffer computed in PostGIS, rasterised in chunks) -------------
    buf = np.zeros(shp, dtype="uint8")
    for chunk in gpd.read_postgis(ROADS_SQL, engine, geom_col="geometry",
                                  params={"buff": p.road_buff}, chunksize=50000):
        shapes_ = [(g, 1) for g in chunk.geometry if g is not None and not g.is_empty]
        if shapes_:
            buf |= features.rasterize(shapes_, out_shape=shp, transform=transform,
                                      fill=0, dtype="uint8")
    r_roads = np.where(buf == 1, 0, 1).astype("float32")

    # --- Criterion 2: land cover -----------------------------------------------------------
    lc = gpd.read_postgis("SELECT * FROM raw.landcover ORDER BY fid", engine, geom_col="geometry")
    code = p.code_field.lower()
    assert code in lc.columns, f"land-cover code field {code!r} not in {list(lc.columns)}"
    levels = [int(x) for x in p.lc_suitable_levels.split(",")]
    lc["suit"] = lc[code].astype(str).str.strip().str[0].astype(int).isin(levels).astype(int)
    r_lc = features.rasterize(list(zip(lc.geometry, lc["suit"])), out_shape=shp,
                              transform=transform, fill=0, dtype="uint8").astype("float32")

    # --- Criterion 3: slope from DEM (file-based source, see README) -----------------------
    with rasterio.open(paths.dem) as src:
        dem = src.read(1, masked=True).astype("float64").filled(np.nan)
        dem_t, dem_crs = src.transform, src.crs
        xres, yres = src.res
    assert dem_crs.is_projected, "DEM must be in a projected CRS (metres)"
    slope = horn_slope_pct(dem, xres, yres)
    slope_grid = np.full(shp, np.nan, dtype="float32")
    reproject(source=slope, destination=slope_grid, src_transform=dem_t, src_crs=dem_crs,
              src_nodata=np.nan, dst_transform=transform, dst_crs=p.crs, dst_nodata=np.nan,
              resampling=Resampling.bilinear)
    missing = int(np.count_nonzero(np.isnan(slope_grid) & mask))
    if missing:
        log.warning("%d study-area cells have no slope value (suitable=%s)",
                    missing, p.missing_slope_suitable)
    r_slope = reclass_slope(slope_grid, p.slope_max, p.missing_slope_suitable)

    # --- Combine ---------------------------------------------------------------------------
    total, weighted = combine(r_roads, r_lc, r_slope, mask, p.w_roads, p.w_landc, p.w_slope)
    grids = {}
    for name, arr in (("roads", r_roads), ("landcover", r_lc), ("slope", r_slope)):
        grids[name] = np.where(mask, arr, NODATA).astype("float32")
    grids["weighted_sum"] = weighted

    # --- Vectorise fully-suitable cells, keep >= min_area ---------------------------------
    full = np.where(mask & (total == 3), 1, 0).astype("uint8")
    geoms = [shape(g) for g, v in features.shapes(full, mask=full.astype(bool), transform=transform) if v == 1]
    suit = gpd.GeoDataFrame(geometry=geoms, crs=p.crs)
    parts = suit.dissolve().explode(index_parts=False).reset_index(drop=True)
    parts["area_m2"] = parts.area
    cand = parts[parts["area_m2"] >= p.min_area].copy().reset_index(drop=True)

    # --- Criterion 4: fully inside urbanisation plan ---------------------------------------
    urban = gpd.read_postgis("SELECT * FROM raw.urban_plan", engine, geom_col="geometry")
    cand["in_urban_plan"] = cand.within(urban.dissolve().geometry.iloc[0])

    # --- Zonal stats of weighted score -----------------------------------------------------
    zs = zonal_stats(cand, weighted, affine=transform, nodata=NODATA, stats=["mean", "max"])
    cand["score_mean"] = [s["mean"] for s in zs]
    cand["score_max"] = [s["max"] for s in zs]

    n = int(mask.sum())
    qa = {"study_cells": n, "missing_slope_cells": missing,
          "pass_roads": int((r_roads[mask] == 1).sum()),
          "pass_landcover": int((r_lc[mask] == 1).sum()),
          "pass_slope": int((r_slope[mask] == 1).sum()),
          "pass_all": int((total[mask] == 3).sum()),
          "max_weighted_score": float(p.w_roads + p.w_landc + p.w_slope),
          "grid_rows": shp[0], "grid_cols": shp[1]}
    log.info("%d candidates >= %.1f ha, %d inside urban plan",
             len(cand), p.min_area / 1e4, int(cand.in_urban_plan.sum()))
    return Result(cand, grids, transform, qa)
