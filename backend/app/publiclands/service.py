"""Public land boundaries from USGS PAD-US (the "Public Access" feature service).

The map asks for a bounding box; the world is cut into a fixed grid, each cell is fetched from USGS once
(paged, filtered to land that could matter for hunting, and simplified), cached in memory and Postgres for
weeks, and the cells covering the box are merged into one GeoJSON FeatureCollection.

PAD-US says who manages a piece of land and the general level of public access. It does not say whether
hunting is allowed, in season or needs a permit, so every feature carries that caveat in the UI. State
wildlife management areas are in the data but have no dedicated code: they are recognised heuristically
(see `classify`)."""
from __future__ import annotations

import asyncio
import json
import logging
import math
import re
import time
from typing import Optional

import httpx
from sqlalchemy.orm import Session

from app.core.database import engine
from app.publiclands.models import PublicLandCell

log = logging.getLogger(__name__)

SERVICE_URL = ("https://services.arcgis.com/v01gqwM5QqNysAAi/arcgis/rest/services/"
               "Public_Access/FeatureServer/0/query")
ATTRIBUTION = "Public land: USGS PAD-US 3.0 (Protected Areas Database of the United States)"
OUT_FIELDS = "Unit_Nm,Mang_Name,Mang_Type,Des_Tp,Pub_Access"
# City, county and regional-district parks are not hunting land and dominate urban areas, so leave them out.
WHERE = "Mang_Type NOT IN ('LOC','DIST')"

MIN_ZOOM = 11            # below this a view spans too much land to draw
MAX_CELLS = 12           # ...and a request may not touch more grid cells than this
PAGE_SIZE = 2000
MAX_PAGES = 5
REQUEST_TIMEOUT_S = 25
CELL_TTL_S = 30 * 24 * 3600
CACHE_SCHEMA = "v1"      # bump when the stored feature shape or filters change

ACCESS = {"OA": "open", "RA": "restricted", "XA": "closed", "UK": "unknown"}

MANAGERS = {
    "USFS": "U.S. Forest Service", "BLM": "Bureau of Land Management", "NPS": "National Park Service",
    "FWS": "U.S. Fish & Wildlife Service", "USACE": "U.S. Army Corps of Engineers", "DOD": "Dept. of Defense",
    "BOR": "Bureau of Reclamation", "TVA": "Tennessee Valley Authority", "DOE": "Dept. of Energy",
    "SFW": "State fish & wildlife agency", "SDNR": "State natural resources dept.", "SPR": "State parks & recreation",
    "SDC": "State conservation dept.", "SLB": "State land board", "SDOL": "State dept. of lands",
    "STAT": "State", "NGO": "Non-governmental organization", "PVT": "Private", "TRIB": "Tribal", "UNK": "Unknown",
}
DESIGNATIONS = {
    "NF": "National Forest", "NG": "National Grassland", "NP": "National Park", "NM": "National Monument",
    "NWR": "National Wildlife Refuge", "WA": "Wilderness Area", "WSA": "Wilderness Study Area",
    "NCA": "National Conservation Area", "WSR": "Wild & Scenic River", "IRA": "Inventoried Roadless Area",
    "RNA": "Research Natural Area", "REC": "Recreation Area", "RMA": "Resource Management Area",
    "SRMA": "Special Recreation Management Area", "ACEC": "Area of Critical Environmental Concern",
    "SP": "State Park", "SREC": "State Recreation Area", "SCA": "State Conservation Area",
    "SW": "State Wilderness", "SDA": "Special Designation Area", "SOTH": "Other State Land",
    "CONE": "Conservation Easement", "PCON": "Private Conservation Land", "FOTH": "Other Federal Land",
    "MIL": "Military Land", "TRIBL": "Tribal Land", "UNK": "Unknown", "UNKE": "Unknown Easement",
}
_WILDLIFE_NAME = re.compile(r"wildlife (management|demonstration|refuge)? ?area|\bWMA\b|game (land|management)", re.I)
_WILDLIFE_AGENCIES = {"SFW"}     # state fish & wildlife agencies


def classify(props: dict) -> str:
    """A coarse land kind used for styling: wildlife | refuge | forest | federal | state | other.

    State wildlife management areas have no code of their own in PAD-US. They appear as state-managed
    conservation areas run by the state fish & wildlife agency (e.g. Arkansas' Bayou Meto, Dagmar), and
    often have no 'Wildlife Management Area' in the name, so this recognises the agency and the name
    together. It is a best guess, not an official WMA flag."""
    mtype, manager, des = props.get("Mang_Type"), props.get("Mang_Name"), props.get("Des_Tp")
    name = props.get("Unit_Nm") or ""
    if mtype == "STAT" and (manager in _WILDLIFE_AGENCIES or _WILDLIFE_NAME.search(name)):
        return "wildlife"
    if des == "NWR" or manager == "FWS":
        return "refuge"
    if des in ("NF", "NG") or manager == "USFS":
        return "forest"
    if mtype == "FED":
        return "federal"
    if mtype == "STAT":
        return "state"
    return "other"


# ---------- geometry simplification ----------

def _point_line_dist(p, a, b) -> float:
    (px, py), (ax, ay), (bx, by) = p, a, b
    dx, dy = bx - ax, by - ay
    seg = dx * dx + dy * dy
    if seg == 0:
        return math.hypot(px - ax, py - ay)
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / seg))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def _rdp(points: list, tol: float) -> list:
    """Douglas-Peucker on an open polyline (iterative, so a 60,000-vertex boundary can't blow the stack)."""
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


def simplify_ring(ring: list, tol_deg: float, lat: float) -> Optional[list]:
    """Simplify a closed [lon, lat] ring to within `tol_deg` (measured in degrees of latitude), or None if
    it collapses to less than a triangle. Longitude is scaled by cos(lat) so the tolerance is the same in
    every direction."""
    if len(ring) < 4:
        return None
    k = math.cos(math.radians(lat))
    pts = [(x * k, y) for x, y in ring[:-1]]
    # split at the point farthest from the start so the closed ring becomes two open lines
    far = max(range(1, len(pts)), key=lambda i: math.hypot(pts[i][0] - pts[0][0], pts[i][1] - pts[0][1]), default=0)
    if far == 0:
        return None
    first, second = _rdp(pts[:far + 1], tol_deg), _rdp(pts[far:] + [pts[0]], tol_deg)
    merged = first[:-1] + second                       # `second` ends back at the start point: already closed
    if len(merged) < 4:
        return None
    return [[round(x / k, 5), round(y, 5)] for x, y in merged]


def simplify_geometry(geom: dict, tol_deg: float, lat: float) -> Optional[dict]:
    """Simplify a Polygon / MultiPolygon, dropping slivers that vanish at this tolerance."""
    if not geom or geom.get("type") not in ("Polygon", "MultiPolygon"):
        return None
    polys = [geom["coordinates"]] if geom["type"] == "Polygon" else geom["coordinates"]
    out = []
    for poly in polys:
        rings = [simplify_ring(r, tol_deg, lat) for r in poly]
        if rings and rings[0]:                      # the outer ring must survive; a lost hole is fine
            out.append([r for r in rings if r])
    if not out:
        return None
    return {"type": "Polygon", "coordinates": out[0]} if len(out) == 1 else {"type": "MultiPolygon", "coordinates": out}


# ---------- grid ----------

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

def normalize_feature(raw: dict, tol: float, lat: float) -> Optional[dict]:
    props = raw.get("properties") or {}
    geom = simplify_geometry(raw.get("geometry"), tol, lat)
    if not geom:
        return None
    manager, des = props.get("Mang_Name") or "", props.get("Des_Tp") or ""
    fid = raw.get("id")
    if fid is None:
        ring = geom["coordinates"][0] if geom["type"] == "Polygon" else geom["coordinates"][0][0]
        fid = f"{props.get('Unit_Nm')}|{des}|{ring[0][0]},{ring[0][1]}"
    return {
        "type": "Feature", "id": fid, "geometry": geom,
        "properties": {
            "name": props.get("Unit_Nm") or "Unnamed public land",
            "manager": MANAGERS.get(manager, manager or "Unknown"),
            "designation": DESIGNATIONS.get(des, des or "Unknown"),
            "access": ACCESS.get(props.get("Pub_Access"), "unknown"),
            "kind": classify(props),
        },
    }


async def _query_pages(client: httpx.AsyncClient, bbox: tuple[float, float, float, float]) -> list[dict]:
    west, south, east, north = bbox
    raw: list[dict] = []
    for page in range(MAX_PAGES):
        r = await client.get(SERVICE_URL, params={
            "where": WHERE, "geometry": f"{west},{south},{east},{north}", "geometryType": "esriGeometryEnvelope",
            "inSR": 4326, "spatialRel": "esriSpatialRelIntersects", "outFields": OUT_FIELDS,
            "returnGeometry": "true", "outSR": 4326, "geometryPrecision": 5, "f": "geojson",
            "resultOffset": page * PAGE_SIZE, "resultRecordCount": PAGE_SIZE,
        }, timeout=REQUEST_TIMEOUT_S)
        r.raise_for_status()
        data = r.json()
        if data.get("error"):
            raise httpx.HTTPError(f"PAD-US service error: {data['error']}")
        feats = data.get("features") or []
        raw.extend(feats)
        exceeded = data.get("exceededTransferLimit") or (data.get("properties") or {}).get("exceededTransferLimit")
        if not (exceeded or len(feats) >= PAGE_SIZE):
            break
    else:
        log.warning("public lands: hit the %d-page limit for bbox %s; some features may be missing", MAX_PAGES, bbox)
    return raw


async def _fetch_cell(tz: int, ix: int, iy: int) -> list[dict]:
    size = cell_deg(tz)
    west, south = ix * size - 180, iy * size - 90
    bbox = (west, south, west + size, south + size)
    lat_c = south + size / 2
    tol = tolerance_deg(tz, lat_c)
    last: Exception | None = None
    for attempt in range(2):
        try:
            async with httpx.AsyncClient() as client:
                raw = await _query_pages(client, bbox)
            break
        except (httpx.HTTPError, ValueError) as e:
            last = e
            if attempt == 0:
                await asyncio.sleep(1.0)
    else:
        raise PublicLandsUnavailable(f"USGS public land service unreachable: {last}") from last
    # simplifying tens of thousands of vertices is CPU-bound: keep it off the event loop
    feats = await asyncio.to_thread(lambda: [f for f in (normalize_feature(x, tol, lat_c) for x in raw) if f])
    log.info("public lands: cell %d/%d/%d -> %d of %d features kept", tz, ix, iy, len(feats), len(raw))
    return feats


class PublicLandsUnavailable(Exception):
    """USGS could not be reached and nothing is cached for the requested area."""


# ---------- cache (memory + Postgres), single-flight ----------
_mem: dict[str, tuple[float, list]] = {}
_inflight: dict[str, asyncio.Future] = {}


def _db_load(key: str) -> Optional[tuple[float, list]]:
    try:
        with Session(engine) as s:
            row = s.get(PublicLandCell, key)
            if row:
                return row.fetched_at, json.loads(row.payload)
    except Exception:
        log.debug("public land cache read failed for %s", key, exc_info=True)
    return None


def _db_store(key: str, ts: float, feats: list) -> None:
    try:
        with Session(engine) as s:
            s.merge(PublicLandCell(key=key, fetched_at=ts, payload=json.dumps(feats, separators=(",", ":"))))
            s.commit()
    except Exception:
        log.warning("could not persist public land cell %s", key, exc_info=True)


async def _refresh_cell(key: str, tz: int, ix: int, iy: int) -> list:
    feats = await _fetch_cell(tz, ix, iy)
    now = time.time()
    _mem[key] = (now, feats)
    _db_store(key, now, feats)
    return feats


async def get_cell(tz: int, ix: int, iy: int) -> tuple[list, bool]:
    """(features, fresh). A cell newer than CELL_TTL_S is returned as-is; an older one is refetched, and if
    USGS is down the old copy is served instead (fresh=False). Raises PublicLandsUnavailable when there is
    nothing to fall back on."""
    key = f"plands:{CACHE_SCHEMA}:{tz}:{ix}:{iy}"
    entry = _mem.get(key) or _db_load(key)
    if entry:
        _mem[key] = entry
        if time.time() - entry[0] < CELL_TTL_S:
            return entry[1], True
    task = _inflight.get(key)
    if task is None or task.done():
        task = asyncio.ensure_future(_refresh_cell(key, tz, ix, iy))
        _inflight[key] = task

        def _cleanup(t: asyncio.Future, key: str = key) -> None:
            if _inflight.get(key) is t:
                del _inflight[key]
            if not t.cancelled():
                t.exception()   # mark retrieved
        task.add_done_callback(_cleanup)
    try:
        return await asyncio.shield(task), True
    except PublicLandsUnavailable:
        if entry:
            log.warning("USGS unavailable; serving stale public land cell %s", key)
            return entry[1], False
        raise


class BadBounds(ValueError):
    """The requested map area is invalid, too zoomed out, or too large."""


async def public_lands(south: float, west: float, north: float, east: float, zoom: int) -> dict:
    """GeoJSON FeatureCollection of public land intersecting the box, plus `attribution` and `partial`."""
    if not (-85 <= south < north <= 85 and -180 <= west < east <= 180):
        raise BadBounds("invalid bounds")
    if zoom < MIN_ZOOM:
        raise BadBounds(f"zoom in to at least level {MIN_ZOOM} to load public land")
    tz = tile_zoom(zoom)
    cells = cells_for_bbox(south, west, north, east, tz)
    if len(cells) > MAX_CELLS:
        raise BadBounds("map area is too large; zoom in")

    results = await asyncio.gather(*(get_cell(tz, x, y) for x, y in cells), return_exceptions=True)
    features: dict = {}
    partial = False
    failures = [r for r in results if isinstance(r, Exception)]
    for res in results:
        if isinstance(res, Exception):
            partial = True
            continue
        feats, fresh = res
        partial = partial or not fresh
        for f in feats:
            features.setdefault(f["id"], f)
    if failures and not features:
        raise failures[0] if isinstance(failures[0], PublicLandsUnavailable) else PublicLandsUnavailable(str(failures[0]))
    return {"type": "FeatureCollection", "features": list(features.values()),
            "attribution": ATTRIBUTION, "partial": partial}
