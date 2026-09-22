"""Off-road trails and roads from the US Forest Service Motor Vehicle Use Map (MVUM).

Same shape as the public-land layer (see app.publiclands.service): the map asks for a bounding box, the
world is cut into a fixed grid, each cell is fetched from USFS once (paged and simplified), cached in
memory and Postgres for weeks, and the cells covering the box are merged into one GeoJSON FeatureCollection.

MVUM only covers land administered by the U.S. Forest Service — it says nothing about state parks, private
OHV parks, county trails, or other federal land (BLM, Corps of Engineers), so coverage outside National
Forest boundaries is genuinely empty, not a bug. Two MVUM layers are combined: designated OHV/ATV/motorcycle
trails, and designated roads (passenger vehicle through high-clearance/truck)."""
from __future__ import annotations

import asyncio
import json
import logging
import math
import time
from typing import Optional

import httpx
from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.core.database import engine
from app.trails.models import TrailCell

log = logging.getLogger(__name__)

_MVUM_BASE = "https://apps.fs.usda.gov/arcx/rest/services/EDW/EDW_MVUM_01/MapServer"
TRAILS_URL = f"{_MVUM_BASE}/2/query"
ROADS_URL = f"{_MVUM_BASE}/1/query"
TRAILS_OUT_FIELDS = "name,motorcycle,atv,seasonal,forestname,jurisdiction"
ROADS_OUT_FIELDS = ("name,passengervehicle,highclearancevehicle,truck,atv,motorcycle,forestname,jurisdiction,"
                     "passengervehicle_datesopen,highclearancevehicle_datesopen,truck_datesopen")
ATTRIBUTION = "Off-road trails & roads: U.S. Forest Service Motor Vehicle Use Map (MVUM) — National Forest land only"

MIN_ZOOM = 11
MAX_CELLS = 12
PAGE_SIZE = 2000
MAX_PAGES = 5
REQUEST_TIMEOUT_S = 25
CELL_TTL_S = 30 * 24 * 3600
EMPTY_CELL_TTL_S = 24 * 3600      # an empty answer is re-checked daily: it may be a real gap or a glitch
PRUNE_AFTER_S = 180 * 24 * 3600   # cells not refreshed in this long are dropped to keep the cache bounded
CACHE_SCHEMA = "v1"      # bump when the stored feature shape or filters change

_ROAD_DATES_FIELD = {
    "Passenger vehicle": "passengervehicle_datesopen",
    "High-clearance vehicle": "highclearancevehicle_datesopen",
    "Truck": "truck_datesopen",
}


def _open_value(v) -> bool:
    """MVUM marks a vehicle class as designated with a non-empty value (typically "OPEN") and leaves the
    field null/blank when that class isn't allowed. Treat anything else as not open, defensively."""
    if v is None:
        return False
    s = str(v).strip().lower()
    return s not in ("", "closed", "no", "not open", "0")


def _trail_vehicle_class(props: dict) -> str:
    atv, moto = _open_value(props.get("atv")), _open_value(props.get("motorcycle"))
    if atv and moto:
        return "ATV & motorcycle"
    if atv:
        return "ATV"
    if moto:
        return "Motorcycle"
    return "Other OHV"


def _road_vehicle_class(props: dict) -> str:
    if _open_value(props.get("passengervehicle")):
        return "Passenger vehicle"
    if _open_value(props.get("highclearancevehicle")):
        return "High-clearance vehicle"
    if _open_value(props.get("truck")):
        return "Truck"
    if _open_value(props.get("atv")) or _open_value(props.get("motorcycle")):
        return "ATV/motorcycle"
    return "Restricted"


def _road_seasonal(props: dict, vehicle_class: str) -> Optional[str]:
    field = _ROAD_DATES_FIELD.get(vehicle_class)
    val = props.get(field) if field else None
    return (val or "").strip() or None


# ---------- geometry simplification (open polylines — no ring-closing needed) ----------

def _point_line_dist(p, a, b) -> float:
    (px, py), (ax, ay), (bx, by) = p, a, b
    dx, dy = bx - ax, by - ay
    seg = dx * dx + dy * dy
    if seg == 0:
        return math.hypot(px - ax, py - ay)
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / seg))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def _rdp(points: list, tol: float) -> list:
    """Douglas-Peucker on an open polyline (iterative, so a very long trail can't blow the stack)."""
    n = len(points)
    if n < 3:
        return list(points)
    keep = [False] * n
    keep[0] = keep[-1] = True
    stack = [(0, n - 1)]
    while stack:
        lo, hi = stack.pop()
        far, far_d = -1, tol
        for i in range(lo + 1, hi):
            d = _point_line_dist(points[i], points[lo], points[hi])
            if d > far_d:
                far, far_d = i, d
        if far != -1:
            keep[far] = True
            stack.append((lo, far))
            stack.append((far, hi))
    return [p for p, k in zip(points, keep) if k]


def simplify_line_coords(coords: list, tol_deg: float, lat: float) -> Optional[list]:
    """Simplify one open [lon, lat] line to within `tol_deg`, or None if it collapses to under 2 points.
    Longitude is scaled by cos(lat) so the tolerance is the same in every direction."""
    if len(coords) < 2:
        return None
    k = math.cos(math.radians(lat))
    pts = [(x * k, y) for x, y in coords]
    simplified = _rdp(pts, tol_deg)
    if len(simplified) < 2:
        return None
    return [[round(x / k, 5), round(y, 5)] for x, y in simplified]


def simplify_line_geometry(geom: dict, tol_deg: float, lat: float) -> Optional[dict]:
    """Simplify a LineString / MultiLineString, dropping any part that collapses at this tolerance."""
    if not geom or geom.get("type") not in ("LineString", "MultiLineString"):
        return None
    if geom["type"] == "LineString":
        line = simplify_line_coords(geom["coordinates"], tol_deg, lat)
        return {"type": "LineString", "coordinates": line} if line else None
    lines = [l for l in (simplify_line_coords(c, tol_deg, lat) for c in geom["coordinates"]) if l]
    if not lines:
        return None
    return {"type": "LineString", "coordinates": lines[0]} if len(lines) == 1 else \
        {"type": "MultiLineString", "coordinates": lines}


# ---------- grid (identical scheme to app.publiclands.service) ----------

def tile_zoom(zoom: int) -> int:
    return min(14, max(9, zoom - 1))


def cell_deg(tz: int) -> float:
    return 360.0 / (2 ** tz)


def cells_for_bbox(south: float, west: float, north: float, east: float, tz: int) -> list[tuple[int, int]]:
    size = cell_deg(tz)
    x0, x1 = math.floor((west + 180) / size), math.floor((east + 180) / size)
    y0, y1 = math.floor((south + 90) / size), math.floor((north + 90) / size)
    return [(x, y) for x in range(x0, x1 + 1) for y in range(y0, y1 + 1)]


def tolerance_deg(tz: int, lat: float) -> float:
    """About 1.5 screen pixels at the lowest zoom that uses this grid level (in degrees of latitude)."""
    meters_per_px = 156543.03 * math.cos(math.radians(lat)) / (2 ** (tz + 1))
    return 1.5 * meters_per_px / 111320.0


# ---------- fetching one cell ----------

def _first_point(geom: dict) -> list:
    return geom["coordinates"][0] if geom["type"] == "LineString" else geom["coordinates"][0][0]


def normalize_trail_feature(raw: dict, tol: float, lat: float) -> Optional[dict]:
    props = raw.get("properties") or {}
    geom = simplify_line_geometry(raw.get("geometry"), tol, lat)
    if not geom:
        return None
    name = (props.get("name") or "").strip() or "Unnamed trail"
    fid = raw.get("id")
    if fid is None:
        p = _first_point(geom)
        fid = f"{name}|{p[0]},{p[1]}"
    return {
        "type": "Feature", "id": f"trail-{fid}", "geometry": geom,
        "properties": {
            "name": name, "kind": "trail", "vehicle_class": _trail_vehicle_class(props),
            "seasonal": (props.get("seasonal") or "").strip() or None,
            "forestname": props.get("forestname") or "", "jurisdiction": props.get("jurisdiction") or "",
            "source": "USFS MVUM",
        },
    }


def normalize_road_feature(raw: dict, tol: float, lat: float) -> Optional[dict]:
    props = raw.get("properties") or {}
    geom = simplify_line_geometry(raw.get("geometry"), tol, lat)
    if not geom:
        return None
    name = (props.get("name") or "").strip() or "Unnamed road"
    fid = raw.get("id")
    if fid is None:
        p = _first_point(geom)
        fid = f"{name}|{p[0]},{p[1]}"
    vehicle_class = _road_vehicle_class(props)
    return {
        "type": "Feature", "id": f"road-{fid}", "geometry": geom,
        "properties": {
            "name": name, "kind": "road", "vehicle_class": vehicle_class,
            "seasonal": _road_seasonal(props, vehicle_class),
            "forestname": props.get("forestname") or "", "jurisdiction": props.get("jurisdiction") or "",
            "source": "USFS MVUM",
        },
    }


async def _query_pages(client: httpx.AsyncClient, bbox: tuple[float, float, float, float], source: str) -> list[dict]:
    west, south, east, north = bbox
    url = TRAILS_URL if source == "trail" else ROADS_URL
    fields = TRAILS_OUT_FIELDS if source == "trail" else ROADS_OUT_FIELDS
    raw: list[dict] = []
    for page in range(MAX_PAGES):
        r = await client.get(url, params={
            "where": "1=1", "geometry": f"{west},{south},{east},{north}", "geometryType": "esriGeometryEnvelope",
            "inSR": 4326, "spatialRel": "esriSpatialRelIntersects", "outFields": fields,
            "returnGeometry": "true", "outSR": 4326, "geometryPrecision": 5, "f": "geojson",
            "resultOffset": page * PAGE_SIZE, "resultRecordCount": PAGE_SIZE,
        }, timeout=REQUEST_TIMEOUT_S)
        r.raise_for_status()
        data = r.json()
        if data.get("error"):
            raise httpx.HTTPError(f"MVUM service error: {data['error']}")
        feats = data.get("features") or []
        raw.extend(feats)
        exceeded = data.get("exceededTransferLimit") or (data.get("properties") or {}).get("exceededTransferLimit")
        if not (exceeded or len(feats) >= PAGE_SIZE):
            break
    else:
        log.warning("trails (%s): hit the %d-page limit for bbox %s; some features may be missing", source, MAX_PAGES, bbox)
    return raw


async def _fetch_cell(source: str, tz: int, ix: int, iy: int) -> list[dict]:
    size = cell_deg(tz)
    west, south = ix * size - 180, iy * size - 90
    bbox = (west, south, west + size, south + size)
    lat_c = south + size / 2
    tol = tolerance_deg(tz, lat_c)
    last: Exception | None = None
    for attempt in range(2):
        try:
            async with httpx.AsyncClient() as client:
                raw = await _query_pages(client, bbox, source)
            break
        except (httpx.HTTPError, ValueError) as e:
            last = e
            if attempt == 0:
                await asyncio.sleep(1.0)
    else:
        raise TrailsUnavailable(f"USFS MVUM {source} service unreachable: {last}") from last
    normalize = normalize_trail_feature if source == "trail" else normalize_road_feature
    feats = await asyncio.to_thread(lambda: [f for f in (normalize(x, tol, lat_c) for x in raw) if f])
    log.info("trails (%s): cell %d/%d/%d -> %d of %d features kept", source, tz, ix, iy, len(feats), len(raw))
    return feats


class TrailsUnavailable(Exception):
    """USFS could not be reached and nothing is cached for the requested area."""


# ---------- cache (memory + Postgres), single-flight ----------
_mem: dict[str, tuple[float, list]] = {}
_inflight: dict[str, asyncio.Future] = {}


def _db_load(key: str) -> Optional[tuple[float, list]]:
    try:
        with Session(engine) as s:
            row = s.get(TrailCell, key)
            if row:
                return row.fetched_at, json.loads(row.payload)
    except Exception:
        log.debug("trail cache read failed for %s", key, exc_info=True)
    return None


def _db_store(key: str, ts: float, feats: list) -> None:
    try:
        with Session(engine) as s:
            s.merge(TrailCell(key=key, fetched_at=ts, payload=json.dumps(feats, separators=(",", ":"))))
            s.commit()
    except Exception:
        log.warning("could not persist trail cell %s", key, exc_info=True)


async def _refresh_cell(key: str, source: str, tz: int, ix: int, iy: int) -> list:
    feats = await _fetch_cell(source, tz, ix, iy)
    now = time.time()
    _mem[key] = (now, feats)
    _db_store(key, now, feats)
    return feats


def _ttl(feats: list) -> float:
    return CELL_TTL_S if feats else EMPTY_CELL_TTL_S


async def get_cell(source: str, tz: int, ix: int, iy: int) -> tuple[list, bool]:
    """(features, fresh). A cell younger than its TTL is returned as-is (30 days; an empty cell only 1 day, so a
    glitchy empty answer can't hide a trail for a month); an older one is refetched, and if the service is down the
    old copy is served instead (fresh=False). Raises TrailsUnavailable when there is nothing to fall back on."""
    key = f"trails:{CACHE_SCHEMA}:{source}:{tz}:{ix}:{iy}"
    entry = _mem.get(key) or _db_load(key)
    if entry:
        _mem[key] = entry
        if time.time() - entry[0] < _ttl(entry[1]):
            return entry[1], True
    task = _inflight.get(key)
    if task is None or task.done():
        task = asyncio.ensure_future(_refresh_cell(key, source, tz, ix, iy))
        _inflight[key] = task

        def _cleanup(t: asyncio.Future, key: str = key) -> None:
            if _inflight.get(key) is t:
                del _inflight[key]
            if not t.cancelled():
                t.exception()   # mark retrieved
        task.add_done_callback(_cleanup)
    try:
        return await asyncio.shield(task), True
    except TrailsUnavailable:
        if entry:
            log.warning("USFS MVUM unavailable; serving stale trail cell %s", key)
            return entry[1], False
        raise


def prune_cache(max_age_s: float = PRUNE_AFTER_S) -> int:
    """Drop cells nobody has needed for a long time (from memory and Postgres); returns how many rows went."""
    cutoff = time.time() - max_age_s
    for k in [k for k, (ts, _f) in _mem.items() if ts < cutoff]:
        del _mem[k]
    try:
        with Session(engine) as s:
            n = s.execute(delete(TrailCell).where(TrailCell.fetched_at < cutoff)).rowcount or 0
            s.commit()
        return n
    except Exception:
        log.debug("trail cache prune failed", exc_info=True)
        return 0


class BadBounds(ValueError):
    """The requested map area is invalid, too zoomed out, or too large."""


async def trails(south: float, west: float, north: float, east: float, zoom: int) -> dict:
    """GeoJSON FeatureCollection of MVUM trails/roads intersecting the box, plus `attribution` and `partial`."""
    if not (-85 <= south < north <= 85 and -180 <= west < east <= 180):
        raise BadBounds("invalid bounds")
    if zoom < MIN_ZOOM:
        raise BadBounds(f"zoom in to at least level {MIN_ZOOM} to load trails")
    tz = tile_zoom(zoom)
    cells = cells_for_bbox(south, west, north, east, tz)
    if len(cells) > MAX_CELLS:
        raise BadBounds("map area is too large; zoom in")

    jobs = [("trail", x, y) for x, y in cells] + [("road", x, y) for x, y in cells]
    results = await asyncio.gather(*(get_cell(src, tz, x, y) for src, x, y in jobs), return_exceptions=True)
    merged: dict = {}
    partial = False
    failures = []
    for (_src, _x, _y), res in zip(jobs, results):
        if isinstance(res, Exception):
            partial = True
            failures.append(res)
            continue
        feats, fresh = res
        partial = partial or not fresh
        for f in feats:
            merged.setdefault(f["id"], f)

    features = list(merged.values())
    if failures and not features:
        raise failures[0] if isinstance(failures[0], TrailsUnavailable) else TrailsUnavailable(str(failures[0]))
    return {"type": "FeatureCollection", "features": features, "attribution": ATTRIBUTION, "partial": partial}
