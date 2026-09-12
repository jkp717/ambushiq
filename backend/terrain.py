"""Terrain analysis: elevation grid -> slope/aspect + cold-air drainage (D-Infinity flow accumulation)."""
from __future__ import annotations
import json
import math
import asyncio
from dataclasses import dataclass, asdict
from typing import Optional
import httpx
import numpy as np
import richdem as rd


GRID = 41           # denser than the artifact (24) — odd number ensures exact center alignment
BOX_M = 800.0
M_PER_DEG_LAT = 111320.0

DR = [-1, -1, -1, 0, 0, 1, 1, 1]
DC = [-1, 0, 1, -1, 1, -1, 0, 1]
D8_BEARING = [315, 0, 45, 270, 90, 225, 180, 135]


def build_sample_grid(lat: float, lon: float):
    half_lat = (BOX_M / 2) / M_PER_DEG_LAT
    m_per_deg_lon = M_PER_DEG_LAT * math.cos(math.radians(lat))
    half_lon = (BOX_M / 2) / m_per_deg_lon
    lats = [lat + half_lat - (2 * half_lat * r) / (GRID - 1) for r in range(GRID)]
    lons = [lon - half_lon + (2 * half_lon * c) / (GRID - 1) for c in range(GRID)]
    cell_m = BOX_M / (GRID - 1)
    return lats, lons, cell_m


USGS_BATCH = 50     # getSamples caps points per request well under 1600
OM_BATCH = 10       # Open-Meteo rejects very large batches (400)


async def _fetch_usgs(client: httpx.AsyncClient, lats, lons) -> list[float]:
    points = [[lons[c], lats[r]] for r in range(len(lats)) for c in range(len(lons))]
    url = "https://elevation.nationalmap.gov/arcgis/rest/services/3DEPElevation/ImageServer/getSamples"
    out: list[Optional[float]] = [None] * len(points)

    semaphore = asyncio.Semaphore(2)
    max_retries = 2        # Number of retries for a transient 502/504 chunk error
    error_threshold = 2    # X: Max allowed total chunk failures before abandoning USGS

    async def fetch_chunk(start):
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
                    # If it's a gateway gateway error, wait a moment and retry
                    if r.status_code in (502, 504, 429) and attempt < max_retries:
                        await asyncio.sleep(0.5 * (attempt + 1))
                        continue
                    r.raise_for_status()
                    samples = r.json().get("samples")
                    if not samples:
                        raise ValueError("usgs empty")
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


async def fetch_terrain(lat: float, lon: float) -> dict:
    lats, lons, cell_m = build_sample_grid(lat, lon)
    async with httpx.AsyncClient() as client:
        try:
            flat = await _fetch_usgs(client, lats, lons)
            source = "USGS 3DEP"
        except Exception as err:
            flat = await _fetch_open_meteo(client, lats, lons)
            source = "Open-Meteo"
    dem = [flat[r * GRID:(r + 1) * GRID] for r in range(GRID)]
    return analyze_terrain(dem, cell_m, source)


def analyze_terrain_d8(dem, cell_m: float, source: str) -> dict:
    """Analyze terrian using the D8 flow accumulation spatial analysis algorithm"""
    n = len(dem)
    ctr = n // 2

    dzdx = (dem[ctr][ctr + 1] - dem[ctr][ctr - 1]) / (2 * cell_m)
    dzdy = (dem[ctr + 1][ctr] - dem[ctr - 1][ctr]) / (2 * cell_m)
    east, south = -dzdx, -dzdy
    downhill_deg = (math.degrees(math.atan2(east, -south)) + 360) % 360
    slope_pct = round(math.hypot(dzdx, dzdy) * 100)

    # D8 flow direction
    direction = [[-1] * n for _ in range(n)]
    for r in range(n):
        for c in range(n):
            # best_slope set slightly below 0 so perfectly flat cells still pick a flow path
            best, best_slope = -1, -1e-6 
            for k in range(8):
                nr, nc = r + DR[k], c + DC[k]
                if nr < 0 or nc < 0 or nr >= n or nc >= n:
                    continue
                dist = cell_m * 1.4142 if DR[k] and DC[k] else cell_m
                slope = (dem[r][c] - dem[nr][nc]) / dist
                if slope > best_slope:
                    best_slope, best = slope, k
            direction[r][c] = best

    # accumulation (Kahn topological order)
    acc = [[1] * n for _ in range(n)]
    indeg = [[0] * n for _ in range(n)]
    for r in range(n):
        for c in range(n):
            k = direction[r][c]
            if k >= 0:
                indeg[r + DR[k]][c + DC[k]] += 1
    queue = [(r, c) for r in range(n) for c in range(n) if indeg[r][c] == 0]
    head = 0
    while head < len(queue):
        r, c = queue[head]
        head += 1
        k = direction[r][c]
        if k >= 0:
            nr, nc = r + DR[k], c + DC[k]
            acc[nr][nc] += acc[r][c]
            indeg[nr][nc] -= 1
            if indeg[nr][nc] == 0:
                queue.append((nr, nc))

    bx = by = acc_sum = 0.0
    max_near = 0
    for r in range(ctr - 3, ctr + 4):
        for c in range(ctr - 3, ctr + 4):
            if r < 0 or c < 0 or r >= n or c >= n:
                continue
            k = direction[r][c]
            if k < 0:
                continue
            w = acc[r][c]
            b = math.radians(D8_BEARING[k])
            bx += math.sin(b) * w
            by += math.cos(b) * w
            acc_sum += w
            max_near = max(max_near, acc[r][c])
            
    # Protect against symmetric vector cancellation resulting in 0,0 inputs to atan2
    if acc_sum > 0 and (abs(bx) > 1e-6 or abs(by) > 1e-6):
        drainage_deg = round((math.degrees(math.atan2(bx, by)) + 360) % 360)
    else:
        drainage_deg = round(downhill_deg)
        
    channel_strength = round(min(1.0, max_near / (n * n * 0.06)) * 100) / 100

    flat_all = [v for row in dem for v in row]
    min_e, max_e = min(flat_all), max(flat_all)

    return {
        "source": source,
        "dem": dem,
        "acc": acc,
        "cell_m": cell_m,
        "downhill_deg": round(downhill_deg),
        "slope_pct": slope_pct,
        "drainage_deg": drainage_deg,
        "channel_strength": channel_strength,
        "elevation": round(dem[ctr][ctr]),
        "relief": round(max_e - min_e),
        "grid_size": n,
        "box_m": BOX_M,
    }


def analyze_terrain(dem, cell_m: float, source: str) -> dict:
    """Analyze terrian using the D-Infinity spatial analysis algorithm"""
    n = len(dem)
    ctr = n // 2

    # Load 2D list into a numpy array and wrap it for RichDEM
    dem_np = np.array(dem, dtype=np.float32)
    rda = rd.rdarray(dem_np, no_data=-9999)

    # Fill artificial sinks to prevent the D-infinity flow from getting trapped
    rd.FillDepressions(rda, epsilon=True, in_place=True)

    # Calculate D-Infinity Flow Accumulation and terrain attributes
    accum_rda = rd.FlowAccumulation(rda, method='Dinf')
    aspect_rda = rd.TerrainAttribute(rda, attrib='aspect')
    slope_rda = rd.TerrainAttribute(rda, attrib='slope_riserun')

    # Extract center-cell metrics, handling flat terrain (-9999 aspect)
    aspect_val = float(aspect_rda[ctr, ctr])
    downhill_deg = aspect_val if aspect_val >= 0 else 0.0
    slope_pct = round((float(slope_rda[ctr, ctr]) / cell_m) * 100)
    
    # Calculate true D-Infinity accumulation drainage vector across the 7x7 center neighborhood
    bx = by = acc_sum = 0.0
    max_near = 0.0
    for r in range(max(0, ctr - 3), min(n, ctr + 4)):
        for c in range(max(0, ctr - 3), min(n, ctr + 4)):
            asp = float(aspect_rda[r, c])
            if asp < 0:
                continue
            w = float(accum_rda[r, c])
            b = math.radians(asp)
            bx += math.sin(b) * w
            by += math.cos(b) * w
            acc_sum += w
            if w > max_near:
                max_near = w
                
    if acc_sum > 0 and (abs(bx) > 1e-6 or abs(by) > 1e-6):
        drainage_deg = round((math.degrees(math.atan2(bx, by)) + 360) % 360)
    else:
        drainage_deg = round(downhill_deg)
                
    channel_strength = round(min(1.0, max_near / (n * n * 0.06)) * 100) / 100

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
        "grid_size": n,
        "box_m": BOX_M,
    }