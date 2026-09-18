import numpy as np
import pytest

from app.scouting import terrain_features
from app.stands import terrain


def _plane(n, cell_m, slope):
    cols = np.arange(n, dtype=np.float32)
    row = 100.0 + slope * cell_m * cols
    return np.tile(row, (n, 1))


@pytest.mark.parametrize("cell_m", [1.0, 10.0, 20.0])
def test_slope_pct_independent_of_cell_size(cell_m):
    dem = _plane(41, cell_m, 0.20)
    out = terrain.analyze_terrain(dem.tolist(), cell_m, "test")
    assert out["slope_pct"] == 20
    assert not out["flat"]


def test_compute_slope_aspect_does_not_mutate_input():
    dem = np.full((21, 21), 50.0, dtype=np.float32)
    dem[8:13, 8:13] = 30.0  # a sink that fill_depressions would raise
    before = dem.copy()
    terrain.compute_slope_aspect(dem, 10.0)
    assert np.array_equal(dem, before)


def test_relief_and_elevation_use_raw_dem():
    dem = np.full((21, 21), 50.0, dtype=np.float32)
    dem[8:13, 8:13] = 8.0
    out = terrain.analyze_terrain(dem.tolist(), 10.0, "test")
    assert out["relief"] == 42
    assert out["elevation"] == 8


def test_flat_ground_is_flagged_not_west():
    dem = np.full((41, 41), 120.0, dtype=np.float32)
    out = terrain.analyze_terrain(dem.tolist(), 20.0, "test")
    assert out["flat"] is True
    assert out["slope_pct"] == 0
    assert out["downhill_deg"] == 0 and out["drainage_deg"] == 0


def test_pinch_detected_between_steep_walls():
    n, cell_m = 60, 20.0
    cols = np.arange(n)
    wall = np.maximum(0, np.abs(cols - 30) - 2) * 0.6 * cell_m
    dem = (wall[None, :] + 0.02 * cell_m * np.arange(n)[:, None]).astype(np.float32)
    grids = terrain_features.funnel_score_grid(
        dem, cell_m, {"scout_steep_slope_pct": 20.0, "scout_max_pinch_width_m": 120.0})
    assert grids["pinch"].max() > 0
