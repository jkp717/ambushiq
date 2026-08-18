"""Terrain analysis: elevation grid -> slope/aspect + cold-air drainage (D8 flow accumulation)."""
from __future__ import annotations
import json
import math
from dataclasses import dataclass, asdict
from typing import Optional
import httpx

GRID = 40           # denser than the artifact (24) — caching makes it affordable
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


USGS_BATCH = 250    # getSamples caps points per request well under 1600
OM_BATCH = 250      # Open-Meteo rejects very large batches (400)


async def _fetch_usgs(client: httpx.AsyncClient, lats, lons) -> list[float]:
    points = [[lons[c], lats[r]] for r in range(len(lats)) for c in range(len(lons))]
    url = "https://elevation.nationalmap.gov/arcgis/rest/services/3DEPElevation/ImageServer/getSamples"
    out: list[Optional[float]] = [None] * len(points)
    for start in range(0, len(points), USGS_BATCH):
        chunk = points[start:start + USGS_BATCH]
        geometry = {"points": chunk, "spatialReference": {"wkid": 4326}}
        # POST the geometry as a form body — too large for a query string.
        data = {
            "geometryType": "esriGeometryMultipoint",
            "geometry": json.dumps(geometry),
            "returnFirstValueOnly": "true",
            "f": "json",
        }
        r = await client.post(url, data=data, timeout=60)
        r.raise_for_status()
        samples = r.json().get("samples")
        if not samples:
            raise ValueError("usgs empty")
        # reassemble by locationId (index within this chunk); order isn't guaranteed
        for s in samples:
            idx = start + int(s["locationId"])
            out[idx] = float(s["value"])
    if any(v is None or math.isnan(v) for v in out):
        raise ValueError("usgs incomplete")
    return out  # type: ignore


async def _fetch_open_meteo(client: httpx.AsyncClient, lats, lons) -> list[float]:
    flat_lats = [round(lats[r], 6) for r in range(len(lats)) for _ in range(len(lons))]
    flat_lons = [round(lons[c], 6) for _ in range(len(lats)) for c in range(len(lons))]
    # Chunked POSTs: one big batch returns 400, so stay under OM_BATCH per call.
    out: list[float] = []
    for start in range(0, len(flat_lats), OM_BATCH):
        r = await client.post(
            "https://api.open-meteo.com/v1/elevation",
            json={"latitude": flat_lats[start:start + OM_BATCH],
                  "longitude": flat_lons[start:start + OM_BATCH]},
            timeout=60,
        )
        r.raise_for_status()
        j = r.json()
        if "elevation" not in j:
            raise ValueError("open-meteo empty")
        out.extend(j["elevation"])
    return out


async def fetch_terrain(lat: float, lon: float) -> dict:
    lats, lons, cell_m = build_sample_grid(lat, lon)
    async with httpx.AsyncClient() as client:
        try:
            flat = await _fetch_usgs(client, lats, lons)
            source = "USGS 3DEP"
        except Exception:
            flat = await _fetch_open_meteo(client, lats, lons)
            source = "Open-Meteo"
    dem = [flat[r * GRID:(r + 1) * GRID] for r in range(GRID)]
    return analyze_terrain(dem, cell_m, source)


def analyze_terrain(dem, cell_m: float, source: str) -> dict:
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
            best, best_slope = -1, 0.0
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
    drainage_deg = round((math.degrees(math.atan2(bx, by)) + 360) % 360) if acc_sum > 0 else round(downhill_deg)
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
