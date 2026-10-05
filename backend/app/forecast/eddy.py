"""Lee-side eddy (rotor) detection.

When wind crosses a ridge with a steep downwind (lee) face, the flow separates at the
crest and a recirculating eddy forms underneath it. Near the ground inside that eddy the
air runs the *opposite* way to the ridge-top wind — back up the lee slope toward the
crest — and it swirls and pulses with the gusts. A hunter at the base of the lee slope
feels this as wind "coming from the wrong direction".

Eddies belong to the terrain + wind, not to a stand, so detection runs on one property-wide
elevation Grid (regions/terrain.py), and each stand reads the point it sits on (an EddySite).
A stand outside that grid falls back to its own 800 m stand-analysis grid. Detection walks
upwind from a point and asks:
  * is there a crest at least `min_ridge_m` above the point,
  * is the point within `reach_ratio` x ridge-height of that crest (the eddy's reach),
  * is some stretch of the lee slope between them steep enough to separate the flow,
  * and is the wind strong enough to drive an eddy.
The same geometry test runs for a single stand and for every grid cell (the lee-eddy map
layer), so the two can never disagree.
"""
from __future__ import annotations

import math
from collections import OrderedDict
from dataclasses import dataclass, field

import numpy as np

DEFAULT_EDDY_PARAMS = {
    "min_ridge_m": 15.0,        # crest must stand at least this far above the point (~50 ft)
    "min_lee_grade": 0.30,      # rise/run of the steepest lee stretch; flow separates above ~30% (~17°)
    "likely_wind_mph": 10.0,    # sustained ridge-top wind that reliably drives an eddy
    "possible_wind_mph": 7.0,   # lighter sustained wind (or gusts past likely_wind_mph) → eddy on gusts
    "reach_ratio": 6.0,         # eddy extends roughly this many ridge-heights downwind of the crest
    "eddy_speed_frac": 0.5,     # near-ground eddy flow strength relative to the ridge-top wind
    "max_walk_m": 1500.0,       # furthest upwind a driving crest is looked for (bounds work + memory)
}

_MASK_CHUNK = 4096             # grid cells per batch when building the map-layer mask
_MASK_CACHE_SIZE = 64

_COMPASS8 = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]


def _compass8(deg: float) -> str:
    return _COMPASS8[round((deg % 360) / 45) % 8]


@dataclass(frozen=True, eq=False)
class Grid:
    """An elevation grid of sample points: row 0 is the north edge (`north` = its latitude),
    col 0 the west edge; rows/cols are `cell_m` apart. `key` identifies this fetch for caching."""
    dem: np.ndarray
    cell_m: float
    north: float
    south: float
    west: float
    east: float
    key: tuple = field(default=())

    def rowcol(self, lat: float, lon: float) -> tuple[float, float]:
        n_r, n_c = self.dem.shape
        return ((self.north - lat) / (self.north - self.south) * (n_r - 1),
                (lon - self.west) / (self.east - self.west) * (n_c - 1))

    def inset_m(self, lat: float, lon: float) -> float:
        """How far (m) the point lies inside the grid's edge (negative = outside)."""
        r, c = self.rowcol(lat, lon)
        n_r, n_c = self.dem.shape
        return min(r, c, n_r - 1 - r, n_c - 1 - c) * self.cell_m

    def bounds(self) -> list[list[float]]:
        """[[south, west], [north, east]] of the cells (sample points ± half a cell), for a map overlay."""
        half_lat = (self.north - self.south) / (self.dem.shape[0] - 1) / 2
        half_lon = (self.east - self.west) / (self.dem.shape[1] - 1) / 2
        return [[self.south - half_lat, self.west - half_lon], [self.north + half_lat, self.east + half_lon]]


@dataclass(frozen=True, eq=False)
class EddySite:
    """The point a stand reads its eddy prediction from: a (fractional) row/col in a grid."""
    dem: np.ndarray
    cell_m: float
    row: float
    col: float


def stand_site(terrain: dict | None) -> EddySite | None:
    """Fallback site: the center of the stand's own 800 m terrain-analysis grid."""
    if not terrain or not terrain.get("dem") or not terrain.get("cell_m"):
        return None
    dem = np.asarray(terrain["dem"], dtype=np.float64)
    return EddySite(dem, float(terrain["cell_m"]), dem.shape[0] // 2, dem.shape[1] // 2)


def site_for(grid: Grid | None, stand: dict, min_inset_m: float = 0.0) -> EddySite | None:
    """The stand's site in the property grid when it sits at least `min_inset_m` inside it,
    else its own stand-analysis grid."""
    if grid is not None and stand.get("lat") is not None and grid.inset_m(stand["lat"], stand["lon"]) >= min_inset_m:
        r, c = grid.rowcol(stand["lat"], stand["lon"])
        return EddySite(grid.dem, grid.cell_m, r, c)
    return stand_site(stand.get("terrain"))


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
                  wind_from_deg: float, max_walk_m: float) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """For each (row, col), walk upwind one cell at a time (up to `max_walk_m`) and return
    (crest height above the point m, crest distance m, steepest lee grade between them)."""
    rad = math.radians(wind_from_deg)
    dr, dc = -math.cos(rad), math.sin(rad)   # one cell toward where the wind comes from
    n_steps = min(int(math.ceil(max(dem.shape) * math.sqrt(2))), int(math.ceil(max_walk_m / cell_m)))
    k = np.arange(n_steps + 1, dtype=np.float64)
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


def detect_lee_eddy(site: EddySite | None, wind_from_deg: float, wind_mph: float, gust_mph: float,
                    params: dict | None = None) -> dict | None:
    """Lee-eddy prediction at a site, or None."""
    p = {**DEFAULT_EDDY_PARAMS, **(params or {})}
    level = _wind_level(wind_mph, gust_mph, p)
    if level is None or site is None:
        return None   # wind checked first: most hours are too calm and skip the terrain walk entirely
    h, d, gr = (float(a[0]) for a in _lee_geometry(site.dem, site.cell_m, np.array([float(site.row)]),
                                                   np.array([float(site.col)]), wind_from_deg, p["max_walk_m"]))
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


_mask_cache: OrderedDict = OrderedDict()


def _mask(grid: Grid, wind_from_deg: int, p: dict) -> np.ndarray:
    """0 = no eddy, 1 = lee eddy zone, 2 = separating crest, for every grid cell. Wind-speed
    independent, so cached per (grid fetch, whole-degree wind direction)."""
    ck = (grid.key, wind_from_deg, tuple(sorted(p.items())))
    if grid.key and ck in _mask_cache:
        _mask_cache.move_to_end(ck)
        return _mask_cache[ck]

    dem, cell_m = grid.dem, grid.cell_m
    n_r, n_c = dem.shape
    rows, cols = (a.ravel() for a in np.meshgrid(np.arange(n_r, dtype=np.float64),
                                                 np.arange(n_c, dtype=np.float64), indexing="ij"))
    lee = np.zeros(rows.size, dtype=bool)
    for s in range(0, rows.size, _MASK_CHUNK):   # chunked so a 200×200 grid stays a few MB
        sl = slice(s, s + _MASK_CHUNK)
        lee[sl] = _in_lee(*_lee_geometry(dem, cell_m, rows[sl], cols[sl], wind_from_deg, p["max_walk_m"]), p)
    lee = lee.reshape(n_r, n_c)
    rows, cols = rows.reshape(n_r, n_c), cols.reshape(n_r, n_c)

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
    mask = np.where(crest, 2, np.where(lee, 1, 0)).astype(np.int8)

    if grid.key:
        _mask_cache[ck] = mask
        while len(_mask_cache) > _MASK_CACHE_SIZE:
            _mask_cache.popitem(last=False)
    return mask


def lee_zone_mask(grid: Grid | None, wind_from_deg: float, wind_mph: float, gust_mph: float,
                  params: dict | None = None) -> dict | None:
    """Map-layer payload for this hour's wind over the property grid:
    {"rows": ["0012…", …] (row 0 = north; 0 none, 1 eddy zone, 2 crest), "bounds": [[s, w], [n, e]]}.
    None when the wind is too light for any eddy, there is no grid, or no eddy anywhere."""
    p = {**DEFAULT_EDDY_PARAMS, **(params or {})}
    if grid is None or _wind_level(wind_mph, gust_mph, p) is None:
        return None
    mask = _mask(grid, round(wind_from_deg) % 360, p)
    if not mask.any():
        return None
    return {"rows": ["".join(map(str, row)) for row in mask.tolist()], "bounds": grid.bounds()}
