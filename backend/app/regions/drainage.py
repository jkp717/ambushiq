"""Property-wide cold-air drainage from the region's elevation grid (regions/terrain.py).

One D-Infinity flow-accumulation pass over the whole property grid feeds two things:
  - the map's "Drainage" layer: the accumulation over all stands plus a margin, and
  - each stand's terrain (downhill/drainage direction, channel strength, slope...) used for scoring,
    the stand editor and the stand list.
Working on the whole grid means a drainage that starts well away from a stand is still counted,
instead of being cut off at the edge of the stand's own 800 m analysis box. A stand's own analysis
(stands/terrain.py) is the fallback when the stand isn't well inside the grid, or the grid came from
90 m Open-Meteo elevation, which is too coarse to read a direction at a stand."""
from __future__ import annotations

import math

import numpy as np
import richdem as rd

from app.forecast.eddy import Grid
from app.regions import terrain
from app.stands.terrain import M_PER_DEG_LAT, RELIEF_HALF_M, compute_slope_aspect, stand_metrics

PROPERTY_SOURCE_LABEL = "USGS 3DEP · property grid"
_LEVELS = 9                 # layer shading levels 1..9 (0 = not drawn)
_MIN_SHADE = 0.15           # log-scaled accumulation at or below this isn't drawn (keeps the map clean)
_analysis_cache: dict[int, tuple[tuple, dict]] = {}   # region id -> (grid key, analysis)


def usable(grid: Grid | None) -> bool:
    """A grid fine enough to read stand terrain from: USGS 3DEP, not the 90 m Open-Meteo fallback."""
    return grid is not None and grid.source.startswith("USGS")


def grid_analysis(grid: Grid) -> dict:
    """Sink-filled slope/aspect and D-Infinity accumulation over the whole grid, computed once per
    grid download (cached by the grid's key)."""
    region_id = grid.key[0] if grid.key else None
    hit = _analysis_cache.get(region_id) if region_id is not None else None
    if hit is not None and hit[0] == grid.key:
        return hit[1]
    rda, slope, aspect = compute_slope_aspect(np.asarray(grid.dem, dtype=np.float32), grid.cell_m)
    out = {
        "slope": np.asarray(slope, dtype=np.float64),
        "aspect": np.asarray(aspect, dtype=np.float64),
        "acc": np.asarray(rd.flow_accumulation(rda, method="Dinf"), dtype=np.float64),
    }
    if region_id is not None:
        _analysis_cache[region_id] = (grid.key, out)
    return out


def stand_terrain(grid: Grid | None, lat: float, lon: float) -> dict | None:
    """The stand's terrain read from the property grid, in the same shape as a stand's own analysis
    (plus `basis: "property"`), or None when the grid isn't usable or the stand isn't well inside it."""
    if not usable(grid) or not terrain.covers(grid, lat, lon):
        return None
    a = grid_analysis(grid)
    rf, cf = grid.rowcol(lat, lon)
    r, c = int(round(rf)), int(round(cf))
    # an ~800 m patch around the stand for the editor's terrain mini-map
    h = round(RELIEF_HALF_M / grid.cell_m)
    win = (slice(max(0, r - h), r + h + 1), slice(max(0, c - h), c + h + 1))
    dem_patch = np.round(np.asarray(grid.dem, dtype=np.float64)[win], 1)
    return {
        "source": PROPERTY_SOURCE_LABEL,
        "basis": "property",
        "dem": dem_patch.tolist(),
        "acc": np.round(a["acc"][win], 1).tolist(),
        "cell_m": grid.cell_m,
        **stand_metrics(grid.dem, a["slope"], a["aspect"], a["acc"], r, c, grid.cell_m),
        "grid_size": dem_patch.shape[0],
        "box_m": (dem_patch.shape[0] - 1) * grid.cell_m,
    }


def with_property_terrain(region_id: int, stands: list[dict]) -> list[dict]:
    """Stand dicts with `terrain` read from the property grid wherever it covers them; elsewhere the
    stand's own analysis is kept (tagged `basis: "stand"`), or None when it has none yet."""
    grid = terrain.load_grid(region_id)
    out = []
    for st in stands:
        prop = stand_terrain(grid, st["lat"], st["lon"]) if st.get("lat") is not None else None
        if prop is not None:
            st = {**st, "terrain": prop}
        elif st.get("terrain"):
            st = {**st, "terrain": {**st["terrain"], "basis": "stand"}}
        out.append(st)
    return out


def covered(region_id: int, lat: float, lon: float) -> bool:
    """Whether a point gets its terrain from the property grid (so needs no analysis of its own)."""
    grid = terrain.load_grid(region_id)
    return usable(grid) and terrain.covers(grid, lat, lon)


def drainage_layer(region_id: int, margin_m: float) -> dict | None:
    """Map-layer payload: the property grid's flow accumulation over every stand plus `margin_m`
    (the region's own point when it has no stands), clipped to the grid:
    {"rows": ["0012…", …] (row 0 = north; 0 = not drawn, 1-9 = log-scaled accumulation),
     "bounds": [[s, w], [n, e]], "cell_m", "fetched_at"}. None with no usable grid or no overlap."""
    grid = terrain.load_grid(region_id)
    if not usable(grid):
        return None
    pts = [(lat, lon) for _, lat, lon in terrain._stand_points(region_id)]
    if not pts:
        clat, clon, _ = terrain.property_box(region_id)
        pts = [(clat, clon)]
    lats, lons = [p[0] for p in pts], [p[1] for p in pts]
    margin = max(0.0, float(margin_m))
    dlat = margin / M_PER_DEG_LAT
    dlon = margin / (M_PER_DEG_LAT * math.cos(math.radians((min(lats) + max(lats)) / 2)))
    n_r, n_c = grid.dem.shape
    r_top, c_left = grid.rowcol(max(lats) + dlat, min(lons) - dlon)
    r_bot, c_right = grid.rowcol(min(lats) - dlat, max(lons) + dlon)
    r0, r1 = max(0, math.floor(r_top)), min(n_r - 1, math.ceil(r_bot))
    c0, c1 = max(0, math.floor(c_left)), min(n_c - 1, math.ceil(c_right))
    if r0 > r1 or c0 > c1:
        return None

    acc = grid_analysis(grid)["acc"][r0:r1 + 1, c0:c1 + 1]
    top = float(np.log1p(max(acc.max(), 0.0)))
    if top <= 0:
        return None
    norm = np.log1p(np.maximum(acc, 0.0)) / top
    levels = np.where(norm <= _MIN_SHADE, 0, np.ceil(norm * _LEVELS)).clip(0, _LEVELS).astype(int)

    lat_step = (grid.north - grid.south) / (n_r - 1)
    lon_step = (grid.east - grid.west) / (n_c - 1)
    bounds = [[grid.north - (r1 + 0.5) * lat_step, grid.west + (c0 - 0.5) * lon_step],
              [grid.north - (r0 - 0.5) * lat_step, grid.west + (c1 + 0.5) * lon_step]]
    return {"rows": ["".join(map(str, row)) for row in levels.tolist()], "bounds": bounds,
            "cell_m": grid.cell_m, "fetched_at": grid.key[1] if len(grid.key) > 1 else None}
