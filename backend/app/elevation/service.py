"""Elevation profile along a straight line, for the map ruler: net change end to end plus the total climb and
drop along the way (a line over a ridge can be level overall but still a long climb on foot).

Points are sampled every ~10 m (2-100 of them) and fetched from USGS 3DEP getSamples in one request (the same
service and request shape as stands.terrain._fetch_usgs, but for an arbitrary list of points instead of a
lat x lon grid). If USGS is down, the region's property grid is used when it covers the line."""
from __future__ import annotations

import asyncio
import json
import math
from collections import OrderedDict
from typing import Optional

import httpx
import numpy as np

from app.regions import terrain as region_terrain
from app.stands.terrain import M_PER_DEG_LAT

USGS_SAMPLES = "https://elevation.nationalmap.gov/arcgis/rest/services/3DEPElevation/ImageServer/getSamples"
STEP_M = 10.0
MIN_SAMPLES, MAX_SAMPLES = 2, 100
NOISE_M = 0.5            # steps smaller than this don't count toward climb/drop (lidar noise)
TIMEOUT_S = 15.0         # 3DEP getSamples answers in ~1.5-13 s depending on load
RETRY_STATUS = {502, 503, 504}
CACHE_SIZE = 256
M_TO_FT = 3.28084

_cache: "OrderedDict[tuple, dict]" = OrderedDict()


class ElevationUnavailable(Exception):
    """Neither USGS nor the property grid could supply the profile."""


def _distance_m(a: tuple[float, float], b: tuple[float, float]) -> float:
    dlat = (b[0] - a[0]) * M_PER_DEG_LAT
    dlon = (b[1] - a[1]) * M_PER_DEG_LAT * math.cos(math.radians((a[0] + b[0]) / 2))
    return math.hypot(dlat, dlon)


def sample_points(a: tuple[float, float], b: tuple[float, float]) -> list[tuple[float, float]]:
    """Evenly spaced (lat, lon) points from a to b inclusive, about STEP_M apart, MIN..MAX_SAMPLES of them."""
    n = max(MIN_SAMPLES, min(MAX_SAMPLES, int(round(_distance_m(a, b) / STEP_M)) + 1))
    return [(a[0] + (b[0] - a[0]) * i / (n - 1), a[1] + (b[1] - a[1]) * i / (n - 1)) for i in range(n)]


def summarize(elev_m: list[float]) -> dict:
    """Net change and total climb / drop (feet) of a profile, ignoring wiggles under NOISE_M."""
    gain = loss = 0.0
    ref = elev_m[0]
    for v in elev_m[1:]:
        d = v - ref
        if abs(d) >= NOISE_M:       # hysteresis: count a step only once it clears the noise band
            if d > 0:
                gain += d
            else:
                loss -= d
            ref = v
    ft = lambda m: round(m * M_TO_FT)
    return {"start_ft": ft(elev_m[0]), "end_ft": ft(elev_m[-1]), "net_ft": ft(elev_m[-1] - elev_m[0]),
            "gain_ft": ft(gain), "loss_ft": ft(loss)}


async def _fetch_usgs(points: list[tuple[float, float]]) -> list[float]:
    geometry = {"points": [[lon, lat] for lat, lon in points], "spatialReference": {"wkid": 4326}}
    data = {"geometryType": "esriGeometryMultipoint", "geometry": json.dumps(geometry),
            "returnFirstValueOnly": "true", "f": "json"}
    async with httpx.AsyncClient(timeout=TIMEOUT_S) as client:
        for attempt in (1, 2):
            try:
                r = await client.post(USGS_SAMPLES, data=data)
            except httpx.TimeoutException as e:     # already waited TIMEOUT_S: don't double it
                raise ElevationUnavailable("timed out") from e
            except httpx.HTTPError as e:
                if attempt == 2:
                    raise ElevationUnavailable(f"{type(e).__name__}") from e
                continue
            if r.status_code in RETRY_STATUS and attempt == 1:
                await asyncio.sleep(0.5)
                continue
            if r.status_code != 200:
                raise ElevationUnavailable(f"HTTP {r.status_code}")
            break
    try:
        samples = r.json().get("samples") or []
        out: list[Optional[float]] = [None] * len(points)
        for s in samples:
            out[int(s["locationId"])] = float(s["value"])
    except (ValueError, KeyError, TypeError) as e:
        raise ElevationUnavailable("bad response") from e
    if any(v is None or math.isnan(v) for v in out):
        raise ElevationUnavailable("incomplete samples")
    return out


def _from_grid(region_id: Optional[int], points: list[tuple[float, float]]) -> Optional[list[float]]:
    """Bilinear elevations from the region's USGS property grid, or None when it doesn't cover every point."""
    grid = region_terrain.load_grid(region_id) if region_id is not None else None
    if grid is None or not grid.source.startswith("USGS"):
        return None
    dem = np.asarray(grid.dem, dtype=np.float64)
    n_r, n_c = dem.shape
    out = []
    for lat, lon in points:
        r, c = grid.rowcol(lat, lon)
        if not (0 <= r <= n_r - 1 and 0 <= c <= n_c - 1):
            return None
        r0, c0 = min(int(r), n_r - 2), min(int(c), n_c - 2)
        fr, fc = r - r0, c - c0
        top = dem[r0, c0] * (1 - fc) + dem[r0, c0 + 1] * fc
        bot = dem[r0 + 1, c0] * (1 - fc) + dem[r0 + 1, c0 + 1] * fc
        out.append(float(top * (1 - fr) + bot * fr))
    return out


async def profile(a: tuple[float, float], b: tuple[float, float], region_id: Optional[int] = None) -> dict:
    """{"samples", "start_ft", "end_ft", "net_ft", "gain_ft", "loss_ft", "source"} for the line a -> b.
    Raises ElevationUnavailable."""
    key = (round(a[0], 5), round(a[1], 5), round(b[0], 5), round(b[1], 5))
    hit = _cache.get(key)
    if hit is not None:
        _cache.move_to_end(key)
        return hit
    points = sample_points(a, b)
    try:
        elev, source = await _fetch_usgs(points), "USGS 3DEP"
    except ElevationUnavailable:
        elev = await asyncio.to_thread(_from_grid, region_id, points)
        if elev is None:
            raise
        source = "property terrain grid"
    out = {"samples": len(points), **summarize(elev), "source": source}
    _cache[key] = out
    if len(_cache) > CACHE_SIZE:
        _cache.popitem(last=False)
    return out
