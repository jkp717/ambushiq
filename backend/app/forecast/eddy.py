"""Lee-side eddy (rotor) detection.

When wind crosses a ridge with a steep downwind (lee) face, the flow separates at the
crest and a recirculating eddy forms underneath it. Near the ground inside that eddy the
air runs the *opposite* way to the ridge-top wind — back up the lee slope toward the
crest — and it swirls and pulses with the gusts. A hunter at the base of the lee slope
feels this as wind "coming from the wrong direction".

Detection walks upwind from a point across the stand's elevation grid (the 2.4 km
context grid when present, else the 800 m analysis grid) and asks:
  * is there a crest at least `min_ridge_m` above the point,
  * is the point within `reach_ratio` x ridge-height of that crest (the eddy's reach),
  * is some stretch of the lee slope between them steep enough to separate the flow,
  * and is the wind strong enough to drive an eddy.
The same geometry test runs for a single stand and for every grid cell (the lee-zone
map layer), so the two can never disagree.
"""
from __future__ import annotations

import math

import numpy as np

DEFAULT_EDDY_PARAMS = {
    "min_ridge_m": 15.0,        # crest must stand at least this far above the point (~50 ft)
    "min_lee_grade": 0.30,      # rise/run of the steepest lee stretch; flow separates above ~30% (~17°)
    "likely_wind_mph": 10.0,    # sustained ridge-top wind that reliably drives an eddy
    "possible_wind_mph": 7.0,   # lighter sustained wind (or gusts past likely_wind_mph) → eddy on gusts
    "reach_ratio": 6.0,         # eddy extends roughly this many ridge-heights downwind of the crest
    "eddy_speed_frac": 0.5,     # near-ground eddy flow strength relative to the ridge-top wind
}

_COMPASS8 = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]


def _compass8(deg: float) -> str:
    return _COMPASS8[round((deg % 360) / 45) % 8]


def _grid(terrain: dict | None) -> tuple[np.ndarray, float] | None:
    """(dem, cell_m) to test on: the wide context grid if fetched, else the analysis grid."""
    if not terrain:
        return None
    src = terrain.get("context") or terrain
    dem = src.get("dem")
    if not dem or not src.get("cell_m"):
        return None
    return np.asarray(dem, dtype=np.float64), float(src["cell_m"])


def _sample(dem: np.ndarray, r: np.ndarray, c: np.ndarray) -> np.ndarray:
    """Bilinear elevation at fractional (row, col); NaN outside the grid."""
    n_r, n_c = dem.shape
    inside = (r >= 0) & (r <= n_r - 1) & (c >= 0) & (c <= n_c - 1)
    r0 = np.clip(np.floor(r), 0, n_r - 2).astype(int)
    c0 = np.clip(np.floor(c), 0, n_c - 2).astype(int)
    fr, fc = np.clip(r - r0, 0, 1), np.clip(c - c0, 0, 1)
    z = (dem[r0, c0] * (1 - fr) * (1 - fc) + dem[r0, c0 + 1] * (1 - fr) * fc
         + dem[r0 + 1, c0] * fr * (1 - fc) + dem[r0 + 1, c0 + 1] * fr * fc)
    return np.where(inside, z, np.nan)


def _lee_geometry(dem: np.ndarray, cell_m: float, rows: np.ndarray, cols: np.ndarray,
                  wind_from_deg: float) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """For each (row, col), walk upwind one cell at a time and return
    (crest height above the point m, crest distance m, steepest lee grade between them).
    Row 0 is the north edge of the grid, col 0 the west edge."""
    rad = math.radians(wind_from_deg)
    dr, dc = -math.cos(rad), math.sin(rad)   # one cell toward where the wind comes from
    k = np.arange(int(math.ceil(max(dem.shape) * math.sqrt(2))) + 1, dtype=np.float64)
    # profile[i, k] = elevation k cells upwind of point i (NaN once the walk leaves the grid)
    profile = _sample(dem, rows.reshape(-1, 1) + k * dr, cols.reshape(-1, 1) + k * dc)
    z0 = profile[:, 0]
    # Going upwind the lee slope *rises*, so a positive grade is a downhill-in-the-wind stretch;
    # grun[:, k] is the steepest such stretch between the point and step k.
    grades = np.diff(profile, axis=1) / cell_m
    grun = np.fmax.accumulate(np.concatenate([np.zeros((len(z0), 1)), np.fmax(grades, 0)], axis=1), axis=1)
    crest = np.argmax(np.where(np.isnan(profile), -np.inf, profile), axis=1)   # first (nearest) highest point
    idx = np.arange(len(z0))
    shape = np.shape(rows)
    return ((profile[idx, crest] - z0).reshape(shape), (crest * cell_m).reshape(shape),
            grun[idx, crest].reshape(shape))


def _in_lee(height, dist, grade, p: dict):
    return (height >= p["min_ridge_m"]) & (dist > 0) & (dist <= p["reach_ratio"] * height) \
        & (grade >= p["min_lee_grade"])


def _wind_level(wind_mph: float, gust_mph: float, p: dict) -> str | None:
    if wind_mph >= p["likely_wind_mph"]:
        return "likely"
    if wind_mph >= p["possible_wind_mph"] or gust_mph >= p["likely_wind_mph"]:
        return "possible"
    return None


def detect_lee_eddy(terrain: dict | None, wind_from_deg: float, wind_mph: float, gust_mph: float,
                    params: dict | None = None) -> dict | None:
    """Lee-eddy prediction for a stand at the center of its terrain grid, or None."""
    p = {**DEFAULT_EDDY_PARAMS, **(params or {})}
    level = _wind_level(wind_mph, gust_mph, p)
    if level is None:
        return None   # checked first: most hours are too calm and skip the terrain walk entirely
    g = _grid(terrain)
    if g is None:
        return None
    dem, cell_m = g
    ctr = np.array([dem.shape[0] // 2], dtype=np.float64)
    h, d, gr = (float(a[0]) for a in _lee_geometry(dem, cell_m, ctr, ctr.copy(), wind_from_deg))
    if math.isnan(h) or not bool(_in_lee(h, d, gr, p)):
        return None

    thr = p["likely_wind_mph"]
    strength = 0.75 + 0.25 * min(1.0, (wind_mph - thr) / 10) if level == "likely" else 0.4
    e = {
        "level": level,
        "strength": round(strength, 2),
        "crest_bearing_deg": round(wind_from_deg % 360),
        "crest_dist_m": round(d),
        "crest_height_m": round(h),
        "lee_grade": round(gr, 2),
        "wind_mph": round(wind_mph, 1),
        "gust_mph": round(gust_mph, 1),
        "threshold_mph": thr,
        "near_ground_to_deg": round(wind_from_deg % 360),   # back upslope, toward the crest
        "speed_frac": p["eddy_speed_frac"],
        "min_lee_grade": p["min_lee_grade"],
    }
    e["text"], e["why"] = explain(e)
    return e


def explain(e: dict) -> tuple[str, str]:
    """Plain-English (text, why) for a detected eddy, in ft / yd / mph."""
    d = _compass8(e["crest_bearing_deg"])
    ft = int(round(e["crest_height_m"] * 3.28084 / 10) * 10)
    yd = int(round(e["crest_dist_m"] * 1.09361 / 10) * 10)
    w, g = round(e["wind_mph"]), round(e["gust_mph"])
    pct, min_pct = round(e["lee_grade"] * 100), round(e["min_lee_grade"] * 100)
    if e["level"] == "likely":
        text = (f"Lee eddy likely — {d} wind ({w} mph, gusts {g}) is spilling over a ridge {ft} ft above you, "
                f"{yd} yd to the {d}. Near the ground expect swirling air drifting back uphill toward the "
                f"{d}, strongest on gusts. Scent direction is unreliable here.")
        wind_why = f"the wind is {w} mph (eddies form above ~{round(e['threshold_mph'])} mph)"
    else:
        text = (f"Lee eddy possible on gusts — {d} wind ({w} mph, gusts {g}) crosses a ridge {ft} ft above you, "
                f"{yd} yd to the {d}. Strong gusts may push air back uphill toward the {d} for a few "
                f"seconds at a time.")
        wind_why = (f"the wind is {w} mph with gusts to {g} — near the ~{round(e['threshold_mph'])} mph "
                    f"where eddies form")
    why = f"Why: you're downwind of steep ground ({pct}% lee slope; flow separates above ~{min_pct}%) and {wind_why}."
    return text, why


def lee_zone_mask(terrain: dict | None, wind_from_deg: float, wind_mph: float, gust_mph: float,
                  params: dict | None = None) -> dict | None:
    """Map-layer grid for this hour's wind over the stand's context grid:
    0 = no eddy, 1 = lee eddy zone, 2 = separating crest. None when the wind is too light
    for any eddy or there is no terrain."""
    p = {**DEFAULT_EDDY_PARAMS, **(params or {})}
    if _wind_level(wind_mph, gust_mph, p) is None:
        return None
    g = _grid(terrain)
    if g is None:
        return None
    dem, cell_m = g
    n_r, n_c = dem.shape
    rows, cols = np.meshgrid(np.arange(n_r, dtype=np.float64), np.arange(n_c, dtype=np.float64), indexing="ij")
    lee = _in_lee(*_lee_geometry(dem, cell_m, rows, cols, wind_from_deg), p)

    # A separating crest: higher than its upwind neighbour, drops steeply on the lee side,
    # and has eddy cells just downwind of it.
    rad = math.radians(wind_from_deg)
    dr, dc = -math.cos(rad), math.sin(rad)
    z_up = _sample(dem, rows + dr, cols + dc)
    z_dn = _sample(dem, rows - dr, cols - dc)
    rd = np.clip(np.rint(rows - dr), 0, n_r - 1).astype(int)
    cd = np.clip(np.rint(cols - dc), 0, n_c - 1).astype(int)
    crest = (np.nan_to_num(dem - z_up, nan=0.0) >= 0) \
        & ((dem - np.nan_to_num(z_dn, nan=np.inf)) / cell_m >= p["min_lee_grade"]) & lee[rd, cd]

    mask = np.where(crest, 2, np.where(lee, 1, 0))
    if not mask.any():
        return None
    src = terrain.get("context") or terrain
    return {"grid": mask.astype(int).tolist(), "grid_size": int(n_r),
            "box_m": float(src.get("box_m") or cell_m * (n_r - 1))}
