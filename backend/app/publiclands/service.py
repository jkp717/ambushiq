"""Public land boundaries from USGS PAD-US (the "Public Access" feature service).

The map asks for a bounding box; the world is cut into a fixed grid, each cell is fetched from USGS once
(paged, filtered to land that could matter for hunting, and simplified), cached in memory and Postgres for
weeks, and the cells covering the box are merged into one GeoJSON FeatureCollection.

PAD-US says who manages a piece of land and the general level of public access. It does not say whether
hunting is allowed, in season or needs a permit, so every feature carries that caveat in the UI. State
wildlife management areas are in the data but have no dedicated code: they are recognised heuristically
(see `classify`), and PAD-US misses some (e.g. Arkansas' Maumelle River WMA). Where a state agency publishes
its own WMA boundaries they are layered in as a second, exact source (see `STATE_SOURCES`): Arkansas (AGFC)
and Illinois (IDNR public hunting areas). The PAD-US features they cover are dropped so the same land isn't
drawn twice."""
from __future__ import annotations

import asyncio
import json
import logging
import math
import re
import time
from dataclasses import dataclass
from typing import Callable, Optional

import httpx
from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.core.database import engine
from app.publiclands.models import PublicLandCell

log = logging.getLogger(__name__)

SERVICE_URL = ("https://services.arcgis.com/v01gqwM5QqNysAAi/arcgis/rest/services/"
               "Public_Access/FeatureServer/0/query")
ATTRIBUTION = "Public land: USGS PAD-US 3.0 (Protected Areas Database of the United States)"
AGFC_ATTRIBUTION = "Arkansas wildlife management areas: Arkansas Game & Fish Commission via the Arkansas GIS Office"
AGFC_URL = ("https://gis.arkansas.gov/arcgis/rest/services/FEATURESERVICES/Boundaries/FeatureServer/37/query")
AGFC_FIELDS = "objectid,fname,flabel,wma"
AGFC_PAGE_SIZE = 200                       # the layer's maxRecordCount (it holds ~140 WMAs in total)
ARKANSAS_BBOX = (-94.7, 33.0, -89.6, 36.6)  # west, south, east, north: cells outside this never ask AGFC

# Illinois has no single WMA layer. Boundaries of IDNR land come from I-View (the Prairie State Conservation
# Coalition's protected-lands inventory, which also lists offices, trails and easements); IDNR's own Illinois
# Public Hunting Area Report (IPHAR, ~260 points with the game hunted at each site) says which of them are
# public hunting areas. Only IDNR polygons matched to an IPHAR site are kept.
IL_ATTRIBUTION = ("Illinois public hunting areas: Illinois DNR (Public Hunting Area Report); boundaries from "
                  "I-View, Prairie State Conservation Coalition")
IL_URL = ("https://services5.arcgis.com/vxriHwYuGDCBCbeh/arcgis/rest/services/"
          "Illinois_Protected_Lands_for_IDNR/FeatureServer/1/query")
IL_WHERE = "Org='Illinois Department of Natural Resources' AND (Owner_Type IS NULL OR Owner_Type NOT LIKE '%Easement%')"
IL_FIELDS = "OBJECTID,Site_Name,Owner_Type"
IL_PAGE_SIZE = 2000
IPHAR_URL = "https://geoservices3.dnr.illinois.gov/arcgis/rest/services/Wildlife/HFSM_Lines_Pts/MapServer/3/query"
IPHAR_FIELDS = "SITENAME,CLASSIFICA,Deer,Turkey,UplandGame,SmallGame,Waterfowl,FurbearerHunting"
ILLINOIS_BBOX = (-91.6, 36.9, -87.0, 42.6)
IPHAR_GAME = [("Deer", "Deer"), ("Turkey", "Turkey"), ("UplandGame", "Upland game"), ("SmallGame", "Small game"),
              ("Waterfowl", "Waterfowl"), ("FurbearerHunting", "Furbearers")]
IL_DESIGNATIONS = {
    "SFWA": "State Fish & Wildlife Area", "SWMA": "State Wildlife Management Area", "WMA": "Wildlife Management Area",
    "SWA": "State Wildlife Area", "SHA": "State Habitat Area", "SP": "State Park", "SRA": "State Recreation Area",
    "SNA": "State Natural Area", "SF": "State Forest", "NP": "Nature Preserve", "ST": "State Trail",
    "SM": "State Memorial", "LWR": "Land & Water Reserve", "PHA": "Public Hunting Area", "FL": "Federal land (IDNR-managed)",
}
OUT_FIELDS = "Unit_Nm,Mang_Name,Mang_Type,Des_Tp,Pub_Access"
# City, county and regional-district parks are not hunting land and dominate urban areas, so leave them out.
WHERE = "Mang_Type NOT IN ('LOC','DIST')"

MIN_ZOOM = 11            # below this a view spans too much land to draw
MAX_CELLS = 12           # ...and a request may not touch more grid cells than this
PAGE_SIZE = 2000
MAX_PAGES = 5
REQUEST_TIMEOUT_S = 25
CELL_TTL_S = 30 * 24 * 3600
EMPTY_CELL_TTL_S = 24 * 3600      # an empty answer is re-checked daily: it may be a real gap or a glitch
PRUNE_AFTER_S = 180 * 24 * 3600   # cells not refreshed in this long are dropped to keep the cache bounded
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
            "source": "USGS PAD-US",
        },
    }


def normalize_agfc_feature(raw: dict, tol: float, lat: float) -> Optional[dict]:
    """An Arkansas Game & Fish Commission WMA polygon. The layer only lists WMAs, so the kind is exact."""
    props = raw.get("properties") or {}
    geom = simplify_geometry(raw.get("geometry"), tol, lat)
    if not geom:
        return None
    name = (props.get("fname") or props.get("flabel") or props.get("wma") or "").strip() or "Wildlife Management Area"
    fid = raw.get("id")
    if fid is None:
        fid = props.get("objectid") if props.get("objectid") is not None else name
    return {
        "type": "Feature", "id": f"agfc-{fid}", "geometry": geom,
        "properties": {
            "name": name, "manager": "Arkansas Game & Fish Commission", "designation": "Wildlife Management Area",
            "access": "restricted",       # public hunting with a license and the WMA's own rules
            "kind": "wildlife", "source": "AGFC",
        },
    }


_SITE_WORDS = re.compile(r"\b(state|fish|and|wildlife|management|area|county|park|recreation|habitat|conservation|"
                         r"natural|forest|the)\b|[^a-z0-9 ]")


def site_key(name: Optional[str]) -> str:
    """A site name reduced to its distinctive words, so I-View's 'Marshall County State Fish and Wildlife Area'
    and IPHAR's 'Marshall State Fish and Wildlife Area' compare equal."""
    return " ".join(_SITE_WORDS.sub(" ", (name or "").lower()).split())


def iphar_site(raw: dict) -> Optional[dict]:
    """One IPHAR point reduced to what the map shows: name, designation, the game IDNR lists, and location."""
    props, geom = raw.get("properties") or {}, raw.get("geometry") or {}
    coords = geom.get("coordinates")
    name = " ".join((props.get("SITENAME") or "").split())
    if not name or not coords:
        return None
    code = (props.get("CLASSIFICA") or "").strip()
    return {
        "name": name, "key": site_key(name), "x": coords[0], "y": coords[1],
        "designation": IL_DESIGNATIONS.get(code, code if len(code) > 4 else "Public hunting area"),
        "game": [label for field, label in IPHAR_GAME if (props.get(field) or "").strip().lower() == "yes"],
    }


def match_site(geom: dict, name: Optional[str], sites: list[dict]) -> Optional[dict]:
    """The IPHAR site for this polygon: the one whose point lies inside it, or failing that the one with the same
    name (a site split into several polygons has its point in only one of them). When one polygon holds several
    IPHAR sites (Pyramid State Park's five units), the one named like the polygon wins; failing that they are
    combined under the polygon's own name."""
    inside = []
    for poly in _polygons(geom):
        xs, ys = [p[0] for p in poly[0]], [p[1] for p in poly[0]]
        x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
        inside += [s for s in sites if s not in inside
                   and x0 <= s["x"] <= x1 and y0 <= s["y"] <= y1 and _in_polygon(s["x"], s["y"], poly)]
    key = site_key(name)
    if len(inside) > 1:
        same = next((s for s in inside if key and s["key"] == key), None)
        if same:
            return same
        game = {g for s in inside for g in s["game"]}
        return {**inside[0], "name": " ".join((name or "").split()) or inside[0]["name"], "key": key,
                "game": [label for _f, label in IPHAR_GAME if label in game]}
    if inside:
        return inside[0]
    return next((s for s in sites if key and s["key"] == key), None)


def normalize_il_feature(raw: dict, tol: float, lat: float, sites: list[dict]) -> Optional[dict]:
    """An IDNR polygon that is a public hunting area in IPHAR; other IDNR land (offices, trails, preserves
    closed to hunting) returns None and is left to PAD-US."""
    props, geom = raw.get("properties") or {}, raw.get("geometry")
    if not geom or geom.get("type") not in ("Polygon", "MultiPolygon"):
        return None
    site = match_site(geom, props.get("Site_Name"), sites)
    if site is None:
        return None
    simple = simplify_geometry(geom, tol, lat)
    if not simple:
        return None
    fid = raw.get("id") if raw.get("id") is not None else props.get("OBJECTID", site["key"])
    return {
        "type": "Feature", "id": f"il-{fid}", "geometry": simple,
        "properties": {
            "name": site["name"], "manager": "Illinois Department of Natural Resources",
            "designation": site["designation"], "access": "restricted", "kind": "wildlife", "source": "IDNR",
            "game": site["game"], "deer": "Deer" in site["game"],
        },
    }


@dataclass(frozen=True)
class StateSource:
    """A state agency's own hunting-area boundaries, layered over PAD-US inside the state's bounding box."""
    key: str                      # cache key segment and feature id prefix
    label: str                    # for logs and errors
    url: str
    where: str
    fields: str
    page_size: int
    bbox: tuple[float, float, float, float]          # west, south, east, north
    attribution: str
    normalize: Callable[..., Optional[dict]]         # (raw, tol, lat[, sites]) -> feature | None
    covers: frozenset             # PAD-US kinds that this source's polygons replace where they overlap
    needs_iphar: bool = False


STATE_SOURCES = {
    "agfc": StateSource("agfc", "Arkansas wildlife area", AGFC_URL, "1=1", AGFC_FIELDS, AGFC_PAGE_SIZE,
                        ARKANSAS_BBOX, AGFC_ATTRIBUTION, normalize_agfc_feature, frozenset({"wildlife"})),
    # IPHAR's hunting sites include state parks and recreation areas, which PAD-US calls plain "state" land
    "il": StateSource("il", "Illinois public hunting area", IL_URL, IL_WHERE, IL_FIELDS, IL_PAGE_SIZE,
                      ILLINOIS_BBOX, IL_ATTRIBUTION, normalize_il_feature, frozenset({"wildlife", "state"}),
                      needs_iphar=True),
}


def _page_size(source: str) -> int:
    return PAGE_SIZE if source == "padus" else STATE_SOURCES[source].page_size


async def _query_pages(client: httpx.AsyncClient, bbox: tuple[float, float, float, float],
                       source: str = "padus") -> list[dict]:
    west, south, east, north = bbox
    if source == "padus":
        url, where, fields = SERVICE_URL, WHERE, OUT_FIELDS
    else:
        src = STATE_SOURCES[source]
        url, where, fields = src.url, src.where, src.fields
    size = _page_size(source)
    raw: list[dict] = []
    for page in range(MAX_PAGES):
        r = await client.get(url, params={
            "where": where, "geometry": f"{west},{south},{east},{north}", "geometryType": "esriGeometryEnvelope",
            "inSR": 4326, "spatialRel": "esriSpatialRelIntersects", "outFields": fields,
            "returnGeometry": "true", "outSR": 4326, "geometryPrecision": 5, "f": "geojson",
            "resultOffset": page * size, "resultRecordCount": size,
        }, timeout=REQUEST_TIMEOUT_S)
        r.raise_for_status()
        data = r.json()
        if data.get("error"):
            raise httpx.HTTPError(f"{_label(source)} service error: {data['error']}")
        feats = data.get("features") or []
        raw.extend(feats)
        exceeded = data.get("exceededTransferLimit") or (data.get("properties") or {}).get("exceededTransferLimit")
        if not (exceeded or len(feats) >= size):
            break
    else:
        log.warning("public lands (%s): hit the %d-page limit for bbox %s; some features may be missing",
                    source, MAX_PAGES, bbox)
    return raw


def _label(source: str) -> str:
    return {"padus": "USGS public land", "iphar": "Illinois hunting area list"}.get(source) or STATE_SOURCES[source].label


async def _with_retry(source: str, fetch: Callable) -> list[dict]:
    last: Exception | None = None
    for attempt in range(2):
        try:
            async with httpx.AsyncClient() as client:
                return await fetch(client)
        except (httpx.HTTPError, ValueError) as e:
            last = e
            if attempt == 0:
                await asyncio.sleep(1.0)
    raise PublicLandsUnavailable(f"{_label(source)} service unreachable: {last}") from last


async def _fetch_iphar() -> list[dict]:
    """Every Illinois public hunting area point (one small statewide request, cached like a cell)."""
    async def fetch(client: httpx.AsyncClient) -> list[dict]:
        r = await client.get(IPHAR_URL, params={
            "where": "1=1", "outFields": IPHAR_FIELDS, "returnGeometry": "true", "outSR": 4326, "f": "geojson",
        }, timeout=REQUEST_TIMEOUT_S)
        r.raise_for_status()
        data = r.json()
        if data.get("error"):
            raise httpx.HTTPError(f"{_label('iphar')} service error: {data['error']}")
        return data.get("features") or []
    sites = [s for s in (iphar_site(x) for x in await _with_retry("iphar", fetch)) if s]
    log.info("public lands (iphar): %d Illinois public hunting areas", len(sites))
    return sites


async def _fetch_cell(source: str, tz: int, ix: int, iy: int) -> list[dict]:
    size = cell_deg(tz)
    west, south = ix * size - 180, iy * size - 90
    bbox = (west, south, west + size, south + size)
    lat_c = south + size / 2
    tol = tolerance_deg(tz, lat_c)
    src = STATE_SOURCES.get(source)
    sites = (await _cached(f"plands:{CACHE_SCHEMA}:iphar", _fetch_iphar))[0] if src and src.needs_iphar else None
    raw = await _with_retry(source, lambda client: _query_pages(client, bbox, source))
    # simplifying tens of thousands of vertices is CPU-bound: keep it off the event loop
    if src is None:
        normalize = normalize_feature
    elif sites is not None:
        normalize = lambda x, t, lat: src.normalize(x, t, lat, sites)  # noqa: E731
    else:
        normalize = src.normalize
    feats = await asyncio.to_thread(lambda: [f for f in (normalize(x, tol, lat_c) for x in raw) if f])
    log.info("public lands (%s): cell %d/%d/%d -> %d of %d features kept", source, tz, ix, iy, len(feats), len(raw))
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


async def _refresh(key: str, fetch: Callable) -> list:
    feats = await fetch()
    now = time.time()
    _mem[key] = (now, feats)
    _db_store(key, now, feats)
    return feats


def _ttl(feats: list) -> float:
    return CELL_TTL_S if feats else EMPTY_CELL_TTL_S


async def get_cell(source: str, tz: int, ix: int, iy: int) -> tuple[list, bool]:
    """(features, fresh) for one grid cell of one source; see `_cached`."""
    key = (f"plands:{CACHE_SCHEMA}:{tz}:{ix}:{iy}" if source == "padus"
           else f"plands:{CACHE_SCHEMA}:{source}:{tz}:{ix}:{iy}")
    return await _cached(key, lambda: _fetch_cell(source, tz, ix, iy))


async def _cached(key: str, fetch: Callable) -> tuple[list, bool]:
    """(items, fresh). An entry younger than its TTL is returned as-is (30 days; an empty one only 1 day, so a
    glitchy empty answer can't hide land for a month); an older one is refetched, and if the service is down the
    old copy is served instead (fresh=False). Raises PublicLandsUnavailable when there is nothing to fall back on."""
    entry = _mem.get(key) or _db_load(key)
    if entry:
        _mem[key] = entry
        if time.time() - entry[0] < _ttl(entry[1]):
            return entry[1], True
    task = _inflight.get(key)
    if task is None or task.done():
        task = asyncio.ensure_future(_refresh(key, fetch))
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
            log.warning("source unavailable; serving stale public land entry %s", key)
            return entry[1], False
        raise


def prune_cache(max_age_s: float = PRUNE_AFTER_S) -> int:
    """Drop cells nobody has needed for a long time (from memory and Postgres); returns how many rows went."""
    cutoff = time.time() - max_age_s
    for k in [k for k, (ts, _f) in _mem.items() if ts < cutoff]:
        del _mem[k]
    try:
        with Session(engine) as s:
            n = s.execute(delete(PublicLandCell).where(PublicLandCell.fetched_at < cutoff)).rowcount or 0
            s.commit()
        return n
    except Exception:
        log.debug("public land cache prune failed", exc_info=True)
        return 0


class BadBounds(ValueError):
    """The requested map area is invalid, too zoomed out, or too large."""


def _polygons(geom: dict) -> list:
    return [geom["coordinates"]] if geom["type"] == "Polygon" else geom["coordinates"]


def _in_polygon(x: float, y: float, rings: list) -> bool:
    """Even-odd ray casting over a polygon's outer ring and holes."""
    inside = False
    for ring in rings:
        j = len(ring) - 1
        for i in range(len(ring)):
            xi, yi, xj, yj = ring[i][0], ring[i][1], ring[j][0], ring[j][1]
            if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
                inside = not inside
            j = i
    return inside


def _agency_index(agency_feats: list) -> list:
    index = []
    for f in agency_feats:
        for poly in _polygons(f["geometry"]):
            xs = [p[0] for p in poly[0]]
            ys = [p[1] for p in poly[0]]
            index.append(((min(xs), min(ys), max(xs), max(ys)), poly))
    return index


def _covered_share(feature: dict, index: list, grid: int = 10) -> float:
    """Roughly what share of this feature's area lies inside a state agency's polygons, from a grid of interior
    sample points. (Sampling the outline instead would be useless here: when two agencies draw the same boundary,
    outline points sit right on the agency's edge and land inside or outside at random.)"""
    polys = _polygons(feature["geometry"])
    xs = [p[0] for poly in polys for p in poly[0]]
    ys = [p[1] for poly in polys for p in poly[0]]
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
    samples = []
    for i in range(grid):
        for j in range(grid):
            x, y = x0 + (i + 0.5) * (x1 - x0) / grid, y0 + (j + 0.5) * (y1 - y0) / grid
            if any(_in_polygon(x, y, poly) for poly in polys):
                samples.append((x, y))
    if not samples:                                  # a sliver too thin for the grid: use one vertex-average point
        ring = polys[0][0]
        samples = [(sum(p[0] for p in ring) / len(ring), sum(p[1] for p in ring) / len(ring))]
    hits = sum(1 for x, y in samples
               if any(b[0] <= x <= b[2] and b[1] <= y <= b[3] and _in_polygon(x, y, poly) for b, poly in index))
    return hits / len(samples)


def drop_covered(padus_feats: list, agency_layers: list[tuple[frozenset, list]]) -> list:
    """PAD-US features of the kinds a state source replaces (e.g. wildlife) that its polygons mostly cover (half
    or more of their area) are duplicates (drawn twice, slightly off); keep the agency's exact outline and any
    PAD-US unit the agency doesn't have."""
    layers = [(kinds, _agency_index(feats)) for kinds, feats in agency_layers if feats]
    if not layers:
        return padus_feats
    return [f for f in padus_feats
            if not any(f["properties"]["kind"] in kinds and _covered_share(f, index) >= 0.5 for kinds, index in layers)]


def _touches(bbox: tuple[float, float, float, float], tz: int, ix: int, iy: int) -> bool:
    size = cell_deg(tz)
    west, south = ix * size - 180, iy * size - 90
    bw, bs, be, bn = bbox
    return west < be and west + size > bw and south < bn and south + size > bs


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

    jobs = [("padus", x, y) for x, y in cells] + [
        (key, x, y) for key, src in STATE_SOURCES.items() for x, y in cells if _touches(src.bbox, tz, x, y)]
    results = await asyncio.gather(*(get_cell(src, tz, x, y) for src, x, y in jobs), return_exceptions=True)
    padus: dict = {}
    agency: dict[str, dict] = {key: {} for key in STATE_SOURCES}
    partial = False
    failures = []
    for (src, _x, _y), res in zip(jobs, results):
        if isinstance(res, Exception):
            partial = True
            failures.append(res)
            continue
        feats, fresh = res
        partial = partial or not fresh
        target = padus if src == "padus" else agency[src]
        for f in feats:
            target.setdefault(f["id"], f)

    agency_feats = [f for by_id in agency.values() for f in by_id.values()]
    features = drop_covered(list(padus.values()),
                            [(STATE_SOURCES[key].covers, list(by_id.values())) for key, by_id in agency.items()])
    features += agency_feats
    if failures and not features:
        raise failures[0] if isinstance(failures[0], PublicLandsUnavailable) else PublicLandsUnavailable(str(failures[0]))
    used = {src for src, _x, _y in jobs}
    attribution = "; ".join([ATTRIBUTION] + [s.attribution for key, s in STATE_SOURCES.items() if key in used])
    return {"type": "FeatureCollection", "features": features, "attribution": attribution, "partial": partial}
