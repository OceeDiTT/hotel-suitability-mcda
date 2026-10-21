import numpy as np
from etl.raster_ops import horn_slope_pct, reclass_slope, combine, NODATA


def test_slope_of_plane():
    # plane rising 0.1 m per 20 m cell eastwards => 0.5 % slope
    dem = np.tile(np.arange(10) * 0.1, (10, 1)).astype("float64")
    s = horn_slope_pct(dem, 20, 20)
    assert np.allclose(s[2:-2, 2:-2], 0.5, atol=1e-3)


def test_slope_flat_is_zero():
    assert horn_slope_pct(np.ones((5, 5)), 20, 20).max() == 0


def test_reclass_and_missing_policy():
    s = np.array([[0, 10, 10.1, np.nan]], dtype="float32")
    assert reclass_slope(s, 10).tolist() == [[1, 1, 0, 0]]
    assert reclass_slope(s, 10, True).tolist() == [[1, 1, 0, 1]]


def test_combine_weights_and_mask():
    one, zero = np.ones((2, 2), "float32"), np.zeros((2, 2), "float32")
    mask = np.array([[True, False], [True, True]])
    tot, w = combine(one, one, zero, mask, 1.0, 2.0, 1.25)
    assert tot[0, 0] == 2 and w[0, 0] == 3.0 and tot[0, 1] == NODATA
    _, w_all = combine(one, one, one, mask, 1.0, 2.0, 1.25)
    assert w_all[0, 0] == 4.25   # max possible score from the notebook
