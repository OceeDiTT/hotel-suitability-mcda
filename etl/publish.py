"""Stage 3 - LOAD results (sites, run metadata, PNG overlays) into schema `analysis`."""
import io
import json
import logging
import numpy as np
from PIL import Image
from rasterio.enums import Resampling
from rasterio.transform import array_bounds
from rasterio.warp import calculate_default_transform, reproject, transform_bounds
from sqlalchemy import text

from .raster_ops import NODATA

log = logging.getLogger("etl.publish")

RDYLGN = [(0.0, (165, 0, 38)), (0.25, (244, 109, 67)), (0.5, (255, 255, 191)),
          (0.75, (166, 217, 106)), (1.0, (0, 104, 55))]

OVERLAYS = {
    "weighted_sum": ("Weighted suitability", "Roads x1 + land cover x2 + slope x1.25 (red = low, green = high)"),
    "roads": ("Criterion: >= 20 m from roads", "Green = outside road buffer, red = inside buffer"),
    "landcover": ("Criterion: land cover", "Green = agriculture / forest & natural, red = artificial, wetland, water"),
    "slope": ("Criterion: slope", "Green = slope within limit, red = steeper or no DEM data"),
}


def _to_mercator(arr, transform, crs):
    h, w = arr.shape
    dt, dw, dh = calculate_default_transform(crs, "EPSG:3857", w, h, *array_bounds(h, w, transform))
    dst = np.full((dh, dw), np.nan, dtype="float32")
    reproject(source=arr.astype("float32"), destination=dst, src_transform=transform, src_crs=crs,
              src_nodata=NODATA, dst_transform=dt, dst_crs="EPSG:3857", dst_nodata=np.nan,
              resampling=Resampling.nearest)
    w_, s, e, n = transform_bounds("EPSG:3857", "EPSG:4326", *array_bounds(dh, dw, dt))  # west, south, east, north
    return dst, (s, w_, n, e)


def _png(arr, name, vmax):
    rgba = np.zeros(arr.shape + (4,), dtype="uint8")
    ok = ~np.isnan(arr)
    if name == "weighted_sum":
        t = np.clip(arr / vmax, 0, 1)
        xs = [x for x, _ in RDYLGN]
        for ch in range(3):
            rgba[..., ch] = np.interp(t, xs, [c[ch] for _, c in RDYLGN])
        rgba[..., 3] = np.where(ok, 170, 0)
    else:
        good = ok & (arr >= 0.5)
        bad = ok & (arr < 0.5)
        rgba[good] = (46, 160, 67, 150)
        rgba[bad] = (214, 39, 40, 110)
    buf = io.BytesIO()
    Image.fromarray(rgba, "RGBA").save(buf, "PNG", optimize=True)
    return buf.getvalue()


def publish(engine, p, paths, res, lineage, label=None):
    cand = res.candidates
    qa = dict(res.qa, n_candidates=int(len(cand)), n_final=int(cand.in_urban_plan.sum()))
    with engine.begin() as c:
        run_id = c.execute(text(
            "INSERT INTO analysis.run (label, params, sources, qa, n_candidates, n_final) "
            "VALUES (:l, CAST(:p AS jsonb), CAST(:s AS jsonb), CAST(:q AS jsonb), :nc, :nf) RETURNING run_id"),
            {"l": label, "p": json.dumps(p.as_dict()), "s": json.dumps(lineage), "q": json.dumps(qa),
             "nc": qa["n_candidates"], "nf": qa["n_final"]}).scalar_one()

        rows = [{"r": run_id, "g": g.wkb, "a": float(a), "m": _f(m), "x": _f(x), "u": bool(u)}
                for g, a, m, x, u in zip(cand.geometry, cand.area_m2, cand.score_mean,
                                         cand.score_max, cand.in_urban_plan)]
        if rows:
            c.execute(text(
                "INSERT INTO analysis.site (run_id, geom, area_m2, score_mean, score_max, in_urban_plan) "
                "VALUES (:r, ST_SetSRID(ST_GeomFromWKB(:g), 3763), :a, :m, :x, :u)"), rows)
        c.execute(text(  # parish of each site = parish containing a point on its surface
            "UPDATE analysis.site s SET parish_name = p.parish_name FROM raw.parishes p "
            "WHERE s.run_id = :r AND p.geometry && ST_PointOnSurface(s.geom) "
            "AND ST_Intersects(p.geometry, ST_PointOnSurface(s.geom))"), {"r": run_id})

        for name, (title, desc) in OVERLAYS.items():
            arr, (s, w, n, e) = _to_mercator(res.grids[name], res.transform, p.crs)
            c.execute(text(
                "INSERT INTO analysis.overlay (run_id,name,title,description,png,south,west,north,east) "
                "VALUES (:r,:n,:t,:d,:png,:s,:w,:no,:e)"),
                {"r": run_id, "n": name, "t": title, "d": desc,
                 "png": _png(arr, name, qa["max_weighted_score"]), "s": s, "w": w, "no": n, "e": e})
    log.info("published run %d (%d candidates, %d final)", run_id, qa["n_candidates"], qa["n_final"])
    return run_id


def _f(v):
    return None if v is None or (isinstance(v, float) and np.isnan(v)) else float(v)
