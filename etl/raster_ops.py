"""Pure-numpy raster maths from the notebook (no GDAL needed, unit-tested)."""
import numpy as np

NODATA = -9999.0


def horn_slope_pct(dem, xres, yres):
    """Slope in percent, Horn's method, edge-padded -- identical to the notebook."""
    zp = np.pad(dem, 1, mode="edge")
    a, b, c = zp[:-2, :-2], zp[:-2, 1:-1], zp[:-2, 2:]
    d, f = zp[1:-1, :-2], zp[1:-1, 2:]
    g, h, i = zp[2:, :-2], zp[2:, 1:-1], zp[2:, 2:]
    dzdx = ((c + 2 * f + i) - (a + 2 * d + g)) / (8 * xres)
    dzdy = ((g + 2 * h + i) - (a + 2 * b + c)) / (8 * yres)
    return (np.hypot(dzdx, dzdy) * 100.0).astype("float32")


def reclass_slope(slope_grid, slope_max, missing_suitable=False):
    """0..slope_max % -> 1, steeper -> 0, NaN -> 0 (or 1 if missing_suitable)."""
    ok = (slope_grid >= 0) & (slope_grid <= slope_max)
    if missing_suitable:
        ok |= np.isnan(slope_grid)
    return ok.astype("float32")


def combine(r_roads, r_lc, r_slope, study_mask, w_roads, w_landc, w_slope):
    """Return (criteria_sum, weighted_sum); cells outside the study area = NODATA."""
    total = r_roads + r_lc + r_slope
    weighted = w_roads * r_roads + w_landc * r_lc + w_slope * r_slope
    total = np.where(study_mask, total, NODATA).astype("float32")
    weighted = np.where(study_mask, weighted, NODATA).astype("float32")
    return total, weighted
