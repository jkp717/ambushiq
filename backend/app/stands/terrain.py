"""Terrain analysis: elevation grid -> slope/aspect + cold-air drainage (D-Infinity flow accumulation)."""
from __future__ import annotations
import json
import math
import asyncio
from typing import Optional
import httpx
import numpy as np
import richdem as rd


DEFAULT_GRID = 41   # denser than the artifact (24) — odd number ensures exact center alignment
DEFAULT_BOX_M = 800.0
M_PER_DEG_LAT = 111320.0

# Rise/run below which a cell is treated as flat: richdem reports aspect 270 for a
# zero gradient, so flat cells must be masked out of every direction average.
FLAT_SLOPE_EPS = 0.005


def build_sample_grid(lat: float, lon: float, grid: int = DEFAULT_GRID, box_m: float = DEFAULT_BOX_M):
    half_lat = (box_m / 2) / M_PER_DEG_LAT
    m_per_deg_lon = M_PER_DEG_LAT * math.cos(math.radians(lat))
    half_lon = (box_m / 2) / m_per_deg_lon
    lats = [lat + half_lat - (2 * half_lat * r) / (grid - 1) for r in range(grid)]
    lons = [lon - half_lon + (2 * half_lon * c) / (grid - 1) for c in range(grid)]
    cell_m = box_m / (grid - 1)
    return lats, lons, cell_m


USGS_BATCH = 100    # getSamples caps points per request well under 1600
OM_BATCH = 10       # Open-Meteo rejects very large batches (400)


async def _fetch_usgs(client: httpx.AsyncClient, lats, lons, progress_callback=None,
                       progress_span: tuple[int, int] = (0, 75)) -> list[float]:
    points = [[lons[c], lats[r]] for r in range(len(lats)) for c in range(len(lons))]
    url = "https://elevation.nationalmap.gov/arcgis/rest/services/3DEPElevation/ImageServer/getSamples"
    out: list[Optional[float]] = [None] * len(points)

    span_lo, span_hi = progress_span
    total_batches = math.ceil(len(points) / USGS_BATCH)
    completed_batches = 0
    semaphore = asyncio.Semaphore(2)
    max_retries = 2         # Number of retries for a transient 502/504 chunk error
    error_threshold = 2     # X: Max allowed total chunk failures before abandoning USGS

    async def fetch_chunk(start):
        nonlocal completed_batches
        chunk = points[start:start + USGS_BATCH]
        geometry = {"points": chunk, "spatialReference": {"wkid": 4326}}
        data = {
            "geometryType": "esriGeometryMultipoint",
            "geometry": json.dumps(geometry),
            "returnFirstValueOnly": "true",
            "f": "json",
        }

        for attempt in range(max_retries + 1):
            async with semaphore:
                try:
                    r = await client.post(url, data=data, timeout=10.0)
                    # If it's a gateway error, wait a moment and retry
                    if r.status_code in (502, 504, 429) and attempt < max_retries:
                        await asyncio.sleep(0.5 * (attempt + 1))
                        continue
                    r.raise_for_status()
                    samples = r.json().get("samples")
                    if not samples:
                        raise ValueError("usgs empty")

                    completed_batches += 1
                    if progress_callback:
                        # USGS accounts for the caller-supplied progress_span of total progress
                        pct = int(span_lo + (completed_batches / total_batches) * (span_hi - span_lo))
                        await progress_callback(pct, f"USGS Batch {completed_batches}/{total_batches}")

                    return start, samples
                except Exception as e:
                    if attempt >= max_retries:
                        raise e
                    await asyncio.sleep(0.5 * (attempt + 1))

    tasks = [fetch_chunk(start) for start in range(0, len(points), USGS_BATCH)]

    # return_exceptions=True captures errors instead of crashing gather instantly
    results = await asyncio.gather(*tasks, return_exceptions=True)

    error_count = 0
    for res in results:
        if isinstance(res, Exception):
            error_count += 1
        else:
            start, samples = res
            for s in samples:
                idx = start + int(s["locationId"])
                out[idx] = float(s["value"])

    # If errors hit or exceed your threshold (X), fail over to Open-Meteo
    if error_count >= error_threshold or any(v is None or math.isnan(v) for v in out):
        raise ValueError(f"usgs failed {error_count} chunks (threshold: {error_threshold})")

    return out


async def _fetch_open_meteo(client: httpx.AsyncClient, lats, lons) -> list[float]:
    flat_lats = [round(lats[r], 6) for r in range(len(lats)) for _ in range(len(lons))]
    flat_lons = [round(lons[c], 6) for _ in range(len(lats)) for c in range(len(lons))]

    out: list[float] = []
    for start in range(0, len(flat_lats), OM_BATCH):
        r = await client.post(
            "https://api.open-meteo.com/v1/elevation",
            json={
                "latitude": flat_lats[start:start + OM_BATCH],
                "longitude": flat_lons[start:start + OM_BATCH]
            },
            timeout=30.0,
        )
        r.raise_for_status()
        j = r.json()
        if "elevation" not in j:
            raise ValueError("open-meteo empty")
        out.extend(j["elevation"])

        # Brief pause between chunks to stay well under Open-Meteo's rate limit
        await asyncio.sleep(0.2)

    return out


async def fetch_terrain(lat: float, lon: float, grid: int = DEFAULT_GRID, box_m: float = DEFAULT_BOX_M,
                         progress_callback=None) -> dict:
    lats, lons, cell_m = build_sample_grid(lat, lon, grid=grid, box_m=box_m)
    if progress_callback:
        await progress_callback(5, "Initializing elevation grid...")

    async with httpx.AsyncClient() as client:
        try:
            flat = await _fetch_usgs(client, lats, lons, progress_callback)
            source = "USGS 3DEP"
        except Exception:
            if progress_callback:
                await progress_callback(40, "USGS limit reached: switching to Open-Meteo...")
            flat = await _fetch_open_meteo(client, lats, lons)
            source = "Open-Meteo"

    if progress_callback:
        await progress_callback(85, "Running D-Infinity terrain analysis...")

    dem = [flat[r * grid:(r + 1) * grid] for r in range(grid)]
    result = analyze_terrain(dem, cell_m, source, box_m=box_m)

    if progress_callback:
        await progress_callback(100, "Complete!")

    return result


def compute_slope_aspect(dem_np, cell_m: float = 1.0):
    """Fill sinks + compute D-Infinity slope/aspect grids. Shared by analyze_terrain()
    and scouting/terrain_features.py so funnel detection can get full per-cell slope
    grids without duplicating this RichDEM setup.

    The caller's array is never modified (RichDEM fills in place, so it works on a
    copy). Returns the sink-filled DEM plus slope (true rise/run) and aspect (degrees
    clockwise from north of the downslope direction) grids."""
    rda = rd.rdarray(np.array(dem_np, dtype=np.float32, copy=True), no_data=-9999)
    # The geotransform carries the cell size, so RichDEM's slope_riserun is already
    # true rise/run (metres per metre) — callers must NOT divide it by cell_m again.
    rda.geotransform = [0, cell_m, 0, 0, 0, -cell_m]
    # Fill artificial sinks to prevent the D-infinity flow from getting trapped
    rd.fill_depressions(rda, epsilon=True, in_place=True)
    slope_rda = rd.terrain_attribute(rda, attrib='slope_riserun')
    aspect_rda = rd.terrain_attribute(rda, attrib='aspect')
    return rda, slope_rda, aspect_rda


def analyze_terrain(dem, cell_m: float, source: str, box_m: float = DEFAULT_BOX_M) -> dict:
    """Analyze terrian using the D-Infinity spatial analysis algorithm"""
    n = len(dem)
    ctr = n // 2

    dem_np = np.array(dem, dtype=np.float32)
    rda, slope_rda, aspect_rda = compute_slope_aspect(dem_np, cell_m)

    # Calculate D-Infinity Flow Accumulation (sinks already filled by compute_slope_aspect)
    accum_rda = rd.flow_accumulation(rda, method='Dinf')

    # slope_riserun is already true rise/run, so percent slope is just ×100.
    slope_pct = max(0, round(float(slope_rda[ctr, ctr]) * 100))

    # downhill_deg and drainage_deg are weighted aspect averages over the 7x7 window
    # around the stand rather than raw single-cell reads. A single pixel is noisy against
    # real DEM error and ordinary stand-pin drift — a few meters shouldn't be able to flip
    # which side of a micro-feature the thermal direction is read from.
    win = slice(max(0, ctr - 3), min(n, ctr + 4))
    slope_w = np.asarray(slope_rda, dtype=np.float64)[win, win]
    asp_w = np.asarray(aspect_rda, dtype=np.float64)[win, win]
    acc_w = np.asarray(accum_rda, dtype=np.float64)[win, win]
    # richdem reports aspect 270 for a zero gradient, so flat cells are masked out of
    # the direction averages; NoData (-9999 slope) falls out via the same test.
    valid = (asp_w >= 0) & (slope_w > FLAT_SLOPE_EPS)
    flat = not bool(valid.any())
    rad = np.radians(asp_w)

    ubx = float(np.sum(np.where(valid, np.sin(rad) * slope_w, 0.0)))
    uby = float(np.sum(np.where(valid, np.cos(rad) * slope_w, 0.0)))
    if not flat and (abs(ubx) > 1e-6 or abs(uby) > 1e-6):
        downhill_deg = (math.degrees(math.atan2(ubx, uby)) + 360) % 360
    else:
        downhill_deg = 0.0

    # D-Infinity accumulation-weighted drainage direction over the same window
    bx = float(np.sum(np.where(valid, np.sin(rad) * acc_w, 0.0)))
    by = float(np.sum(np.where(valid, np.cos(rad) * acc_w, 0.0)))
    if not flat and (abs(bx) > 1e-6 or abs(by) > 1e-6):
        drainage_deg = round((math.degrees(math.atan2(bx, by)) + 360) % 360)
    else:
        drainage_deg = round(downhill_deg)

    aspect_ok = asp_w >= 0
    max_near = float(acc_w[aspect_ok].max()) if aspect_ok.any() else 0.0
    channel_strength = round(min(1.0, max_near / (n * n * 0.06)) * 100) / 100

    # relief is measured on the raw DEM — the sink-filled copy would understate it
    min_e = float(np.min(dem_np))
    max_e = float(np.max(dem_np))

    return {
        "source": source,
        "dem": dem,
        "acc": accum_rda.tolist(),
        "cell_m": cell_m,
        "downhill_deg": round(downhill_deg),
        "slope_pct": slope_pct,
        "drainage_deg": drainage_deg,
        "channel_strength": channel_strength,
        "elevation": round(dem[ctr][ctr]),
        "relief": round(max_e - min_e),
        "flat": flat,
        "grid_size": n,
        "box_m": box_m,
    }