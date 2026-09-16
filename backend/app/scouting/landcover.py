"""NLCD land-cover fetch + forest/open habitat-edge detection.

Fetches land-cover classification for a lat/lon sample grid via NLCD's ArcGIS
ImageServer getSamples operation — the same request shape as
stands.terrain._fetch_usgs's elevation fetch, against a different service.
"""
from __future__ import annotations

import asyncio
import json
import math

import httpx
import numpy as np

NLCD_URL = "https://di-nlcd.img.arcgis.com/arcgis/rest/services/USA_NLCD_Annual_LandCover/ImageServer/getSamples"
NLCD_BATCH = 100

# NLCD Anderson Level II class codes -> coarse habitat bucket, for edge detection.
NLCD_CLASS_MAP = {
    11: "water", 12: "water",
    21: "developed", 22: "developed", 23: "developed", 24: "developed",
    31: "open",
    41: "forest", 42: "forest", 43: "forest",
    51: "open", 52: "open",
    71: "open", 72: "open", 73: "open", 74: "open",
    81: "open", 82: "open",   # pasture/crops — a real edge signal even without crop-specific data
    90: "wetland", 95: "wetland",
}


def classify(nlcd_code) -> str:
    try:
        code = int(nlcd_code)
    except (TypeError, ValueError):
        return "unknown"
    return NLCD_CLASS_MAP.get(code, "unknown")


async def fetch_landcover(client: httpx.AsyncClient, lats, lons, progress_callback=None,
                           progress_span: tuple[int, int] = (48, 80)) -> list[int]:
    """Fetch raw NLCD class codes for the given lat/lon grid, in the same row-major
    point order as stands.terrain.build_sample_grid. Same batching/retry/semaphore
    shape as _fetch_usgs, against NLCD's getSamples instead of 3DEP's."""
    points = [[lons[c], lats[r]] for r in range(len(lats)) for c in range(len(lons))]
    out: list[int | None] = [None] * len(points)

    span_lo, span_hi = progress_span
    total_batches = math.ceil(len(points) / NLCD_BATCH)
    completed_batches = 0
    semaphore = asyncio.Semaphore(2)
    max_retries = 2
    error_threshold = 2

    async def fetch_chunk(start):
        nonlocal completed_batches
        chunk = points[start:start + NLCD_BATCH]
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
                    r = await client.post(NLCD_URL, data=data, timeout=10.0)
                    if r.status_code in (502, 504, 429) and attempt < max_retries:
                        await asyncio.sleep(0.5 * (attempt + 1))
                        continue
                    r.raise_for_status()
                    samples = r.json().get("samples")
                    if not samples:
                        raise ValueError("nlcd empty")
                    completed_batches += 1
                    if progress_callback:
                        pct = int(span_lo + (completed_batches / total_batches) * (span_hi - span_lo))
                        await progress_callback(pct, f"Land cover batch {completed_batches}/{total_batches}")
                    return start, samples
                except Exception as e:
                    if attempt >= max_retries:
                        raise e
                    await asyncio.sleep(0.5 * (attempt + 1))

    tasks = [fetch_chunk(start) for start in range(0, len(points), NLCD_BATCH)]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    error_count = 0
    for res in results:
        if isinstance(res, Exception):
            error_count += 1
        else:
            start, samples = res
            for s in samples:
                idx = start + int(s["locationId"])
                out[idx] = int(float(s["value"]))

    if error_count >= error_threshold or any(v is None for v in out):
        raise ValueError(f"nlcd fetch failed {error_count} chunks (threshold: {error_threshold})")

    return out


def edge_score_grid(nlcd_flat: list[int], grid: int) -> np.ndarray:
    """(grid,grid) array in [0,1]: fraction of a cell's 4 cardinal neighbors that sit
    on the opposite side of a forest<->open boundary — whitetail "edge habitat"."""
    classes = np.array([classify(v) for v in nlcd_flat]).reshape(grid, grid)
    is_forest = classes == "forest"
    is_open = classes == "open"
    out = np.zeros((grid, grid), dtype=np.float32)
    for dr, dc in ((-1, 0), (1, 0), (0, -1), (0, 1)):
        sf = np.roll(is_forest, (dr, dc), axis=(0, 1))
        so = np.roll(is_open, (dr, dc), axis=(0, 1))
        cross = (is_forest & so) | (is_open & sf)
        # np.roll wraps around edges — drop the wrapped-in edge so it doesn't
        # falsely score against the far side of the grid.
        if dr == -1:
            cross[-1, :] = False
        elif dr == 1:
            cross[0, :] = False
        if dc == -1:
            cross[:, -1] = False
        elif dc == 1:
            cross[:, 0] = False
        out += cross.astype(np.float32)
    return out / 4.0
