"""Property-wide elevation grid for lee-eddy detection: one grid per region, sized to cover
every stand plus a margin for upwind ridges, fetched by the "Analyze property terrain" job
and kept up to date automatically as stands are added or moved."""
from __future__ import annotations

import json
import logging
import math
from datetime import datetime, timezone

import httpx
import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import engine
from app.forecast.eddy import Grid
from app.regions.models import Region, RegionTerrain
from app.stands.models import Stand
from app.stands.terrain import M_PER_DEG_LAT, _fetch_open_meteo, _fetch_usgs, build_sample_grid

MARGIN_M = 1500.0       # upwind ridges this far out can still drive an eddy over a stand
MIN_BOX_M = 5000.0
MAX_BOX_M = 8000.0
TARGET_CELL_M = 30.0
MAX_POINTS = 201        # per side — cell size grows past TARGET_CELL_M to stay under this
OPEN_METEO_CELL_M = 90.0   # Open-Meteo's own DEM resolution; keeps the fallback's request count sane
COVER_TOLERANCE = 0.95  # a stand counts as covered at 95% of the margin (float slop at the box edge)

log = logging.getLogger(__name__)
_grid_cache: dict[int, Grid] = {}


def _stand_points(region_id: int) -> list[tuple[str, float, float]]:
    with Session(engine) as s:
        return [(st.name, st.lat, st.lon) for st in s.scalars(
            select(Stand).where(Stand.region_id == region_id)).all()]


def _box_grid(clat: float, clon: float, side: float) -> Grid:
    """A placeholder 2×2 Grid spanning a box — enough for inset/coverage checks before fetching."""
    half_lat = (side / 2) / M_PER_DEG_LAT
    half_lon = (side / 2) / (M_PER_DEG_LAT * math.cos(math.radians(clat)))
    return Grid(dem=np.zeros((2, 2)), cell_m=side, north=clat + half_lat, south=clat - half_lat,
                west=clon - half_lon, east=clon + half_lon)


def property_box(region_id: int) -> tuple[float, float, float]:
    """(center lat, center lon, side m) of the square to fetch: every stand plus MARGIN_M,
    at least MIN_BOX_M and at most MAX_BOX_M across. With no stands, the region's own point."""
    pts = _stand_points(region_id)
    if pts:
        lats, lons = [p[1] for p in pts], [p[2] for p in pts]
        clat, clon = (min(lats) + max(lats)) / 2, (min(lons) + max(lons)) / 2
        span_m = max((max(lats) - min(lats)) * M_PER_DEG_LAT,
                      (max(lons) - min(lons)) * M_PER_DEG_LAT * math.cos(math.radians(clat)))
    else:
        with Session(engine) as s:
            r = s.get(Region, region_id)
            clat, clon = (r.lat, r.lon) if r else (0.0, 0.0)
        span_m = 0.0
    return clat, clon, min(MAX_BOX_M, max(MIN_BOX_M, span_m + 2 * MARGIN_M))


def load_grid(region_id: int) -> Grid | None:
    """The region's stored property grid (cached in-process per fetch), or None."""
    with Session(engine) as s:
        fetched_at = s.scalars(select(RegionTerrain.fetched_at).where(RegionTerrain.region_id == region_id)).first()
        if fetched_at is None:
            _grid_cache.pop(region_id, None)
            return None
        cached = _grid_cache.get(region_id)
        if cached is not None and cached.key == (region_id, fetched_at):
            return cached
        row = s.get(RegionTerrain, region_id)
        grid = Grid(dem=np.asarray(json.loads(row.dem_json), dtype=np.float64), cell_m=row.cell_m,
                    north=row.north, south=row.south, west=row.west, east=row.east,
                    key=(region_id, row.fetched_at), source=row.source or "")
    _grid_cache[region_id] = grid
    return grid


def covers(grid: Grid | None, lat: float, lon: float) -> bool:
    return grid is not None and grid.inset_m(lat, lon) >= MARGIN_M * COVER_TOLERANCE


def needs_refresh(region_id: int) -> bool:
    """No grid yet, or a stand sits outside the stored grid that a re-fetch *would* cover
    (a stand too far to fit in MAX_BOX_M can't be fixed by re-fetching, so it doesn't retrigger)."""
    grid = load_grid(region_id)
    pts = _stand_points(region_id)
    if grid is None:
        return True
    outside = [p for p in pts if not covers(grid, p[1], p[2])]
    if not outside:
        return False
    new_box = _box_grid(*property_box(region_id))
    return any(covers(new_box, lat, lon) for _, lat, lon in outside)


def ensure_property_terrain(region_id: int) -> None:
    """Start the property-terrain job in the background when the grid is missing or a stand
    needs it to grow. Must be called from inside the event loop. Never raises."""
    from app.jobs import service as jobs
    try:
        if needs_refresh(region_id):
            jobs.start_background("property_terrain", region_id)
    except jobs.JobBusy:
        pass
    except Exception:
        log.exception("property terrain check failed for region %s", region_id)


async def fetch_property_terrain(progress, region_id: int) -> str:
    """Job: fetch and store the region's property grid. USGS 3DEP at ~30 m, else Open-Meteo at 90 m."""
    clat, clon, side = property_box(region_id)
    n = min(MAX_POINTS, int(round(side / TARGET_CELL_M)) + 1)

    async def cb(pct, _msg):
        progress.begin(f"Downloading elevation · {pct}%")

    async with httpx.AsyncClient() as client:
        try:
            lats, lons, cell_m = build_sample_grid(clat, clon, grid=n, box_m=side)
            flat = await _fetch_usgs(client, lats, lons, cb, progress_span=(0, 100))
            source = "USGS 3DEP"
        except Exception:
            progress.begin("USGS unavailable — using Open-Meteo")
            n = int(round(side / OPEN_METEO_CELL_M)) + 1
            lats, lons, cell_m = build_sample_grid(clat, clon, grid=n, box_m=side)
            flat = await _fetch_open_meteo(client, lats, lons)
            source = "Open-Meteo"
    if any(v is None for v in flat):
        raise ValueError(f"{source} returned incomplete elevation data")

    dem = [flat[r * n:(r + 1) * n] for r in range(n)]
    with Session(engine) as s:
        row = s.get(RegionTerrain, region_id) or RegionTerrain(region_id=region_id)
        row.north, row.south, row.west, row.east = lats[0], lats[-1], lons[0], lons[-1]
        row.rows = row.cols = n
        row.cell_m, row.source = cell_m, source
        row.dem_json = json.dumps(dem)
        row.fetched_at = datetime.now(timezone.utc).isoformat()
        s.merge(row)
        s.commit()

    grid = load_grid(region_id)
    pts = _stand_points(region_id)
    outside = [name for name, lat, lon in pts if not covers(grid, lat, lon)]
    msg = (f"{side / 1609.344:.1f} mi box · {cell_m / 0.3048:.0f} ft cells · "
           f"covers {len(pts) - len(outside)} of {len(pts)} stands")
    if outside:
        msg += f" ({', '.join(outside[:3])}{'…' if len(outside) > 3 else ''} too far out)"
    # stands this grid can't supply terrain for (outside it, or an Open-Meteo grid) get their own analysis
    from app.stands.service import auto_analyze_uncovered   # stands.service -> regions.drainage -> here
    started = auto_analyze_uncovered(region_id)
    if started:
        msg += f" · analyzing {started} stand(s) on their own"
    return msg
