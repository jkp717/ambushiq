"""Recreation sites (trailheads, campgrounds, picnic sites, day-use areas) from the US Forest Service
EDW "Recreation Sites INFRA" inventory — the same USFS Enterprise Data Warehouse family as the off-road
trails/roads layer (see app.trails.service), and the natural place to look for where to park or start a
hike on National Forest land.

Same shape as the trails/public-land layers: the map asks for a bounding box, the world is cut into a
fixed grid, each cell is fetched from USFS once, cached in memory and Postgres for weeks, and the cells
covering the box are merged into one GeoJSON FeatureCollection. Points need no geometry simplification,
so this module is simpler than app.trails.service / app.publiclands.service.

MVUM-style coverage caveat applies here too: this only covers land administered by the U.S. Forest
Service — nothing on state parks, WMAs, BLM, or other non-NFS land."""
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
from app.recsites.models import RecSiteCell

log = logging.getLogger(__name__)

SERVICE_URL = "https://apps.fs.usda.gov/arcx/rest/services/EDW/EDW_InfraRecreationSites_01/MapServer/0/query"
OUT_FIELDS = ("site_name,site_subtype,recarea_name,directions,fee_charged,fee_description,open_season,"
              "best_season,restrictions,aba_accessible,max_nbr_vehicles,water_availability,restroom_availability")
SITE_KIND = {
    "TRAILHEAD": "trailhead", "CAMPGROUND": "campground",
    "PICNIC SITE": "picnic", "DAY USE AREA": "day_use",
}
WHERE = "site_subtype IN ('" + "','".join(SITE_KIND) + "')"
ATTRIBUTION = "Recreation sites: U.S. Forest Service (National Forest land only)"

MIN_ZOOM = 11
MAX_CELLS = 12
PAGE_SIZE = 2000
MAX_PAGES = 5
REQUEST_TIMEOUT_S = 25
CELL_TTL_S = 30 * 24 * 3600
EMPTY_CELL_TTL_S = 24 * 3600      # an empty answer is re-checked daily: it may be a real gap or a glitch
PRUNE_AFTER_S = 180 * 24 * 3600   # cells not refreshed in this long are dropped to keep the cache bounded
CACHE_SCHEMA = "v1"      # bump when the stored feature shape or filters change


def _truthy(v) -> bool:
    if v is None:
        return False
    return str(v).strip().lower() in ("y", "yes", "true", "1")


def normalize_feature(raw: dict) -> Optional[dict]:
    props = raw.get("properties") or {}
    geom = raw.get("geometry")
    if not geom or geom.get("type") != "Point":
        return None
    subtype = (props.get("site_subtype") or "").strip().upper()
    kind = SITE_KIND.get(subtype)
    if not kind:
        return None
    name = (props.get("site_name") or "").strip() or "Unnamed site"
    fid = raw.get("id")
    if fid is None:
        lon, lat = geom["coordinates"][0], geom["coordinates"][1]
        fid = f"{name}|{lon},{lat}"
    fee_desc = (props.get("fee_description") or "").strip()
    fee = fee_desc or ("Fee charged" if _truthy(props.get("fee_charged")) else None)
    return {
        "type": "Feature", "id": f"rec-{fid}", "geometry": geom,
        "properties": {
            "name": name, "kind": kind,
            "recarea": (props.get("recarea_name") or "").strip() or None,
            "directions": (props.get("directions") or "").strip() or None,
            "fee": fee,
            "season": (props.get("open_season") or props.get("best_season") or "").strip() or None,
            "restrictions": (props.get("restrictions") or "").strip() or None,
            "accessible": _truthy(props.get("aba_accessible")),
            "max_vehicles": props.get("max_nbr_vehicles"),
            "water": (props.get("water_availability") or "").strip() or None,
            "restroom": (props.get("restroom_availability") or "").strip() or None,
            "source": "USFS EDW",
        },
    }


# ---------- grid (identical scheme to app.trails.service / app.publiclands.service) ----------

def tile_zoom(zoom: int) -> int:
    return min(14, max(9, zoom - 1))


def cell_deg(tz: int) -> float:
    return 360.0 / (2 ** tz)


def cells_for_bbox(south: float, west: float, north: float, east: float, tz: int) -> list[tuple[int, int]]:
    size = cell_deg(tz)
    x0, x1 = math.floor((west + 180) / size), math.floor((east + 180) / size)
    y0, y1 = math.floor((south + 90) / size), math.floor((north + 90) / size)
    return [(x, y) for x in range(x0, x1 + 1) for y in range(y0, y1 + 1)]


# ---------- fetching one cell ----------

async def _query_pages(client: httpx.AsyncClient, bbox: tuple[float, float, float, float]) -> list[dict]:
    west, south, east, north = bbox
    raw: list[dict] = []
    for page in range(MAX_PAGES):
        r = await client.get(SERVICE_URL, params={
            "where": WHERE, "geometry": f"{west},{south},{east},{north}", "geometryType": "esriGeometryEnvelope",
            "inSR": 4326, "spatialRel": "esriSpatialRelIntersects", "outFields": OUT_FIELDS,
            "returnGeometry": "true", "outSR": 4326, "f": "geojson",
            "resultOffset": page * PAGE_SIZE, "resultRecordCount": PAGE_SIZE,
        }, timeout=REQUEST_TIMEOUT_S)
        r.raise_for_status()
        data = r.json()
        if data.get("error"):
            raise httpx.HTTPError(f"USFS recreation sites service error: {data['error']}")
        feats = data.get("features") or []
        raw.extend(feats)
        exceeded = data.get("exceededTransferLimit") or (data.get("properties") or {}).get("exceededTransferLimit")
        if not (exceeded or len(feats) >= PAGE_SIZE):
            break
    else:
        log.warning("recreation sites: hit the %d-page limit for bbox %s; some features may be missing", MAX_PAGES, bbox)
    return raw


async def _fetch_cell(tz: int, ix: int, iy: int) -> list[dict]:
    size = cell_deg(tz)
    west, south = ix * size - 180, iy * size - 90
    bbox = (west, south, west + size, south + size)
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
        raise RecSitesUnavailable(f"USFS recreation sites service unreachable: {last}") from last
    feats = [f for f in (normalize_feature(x) for x in raw) if f]
    log.info("recreation sites: cell %d/%d/%d -> %d of %d features kept", tz, ix, iy, len(feats), len(raw))
    return feats


class RecSitesUnavailable(Exception):
    """USFS could not be reached and nothing is cached for the requested area."""


# ---------- cache (memory + Postgres), single-flight ----------
_mem: dict[str, tuple[float, list]] = {}
_inflight: dict[str, asyncio.Future] = {}


def _db_load(key: str) -> Optional[tuple[float, list]]:
    try:
        with Session(engine) as s:
            row = s.get(RecSiteCell, key)
            if row:
                return row.fetched_at, json.loads(row.payload)
    except Exception:
        log.debug("recreation site cache read failed for %s", key, exc_info=True)
    return None


def _db_store(key: str, ts: float, feats: list) -> None:
    try:
        with Session(engine) as s:
            s.merge(RecSiteCell(key=key, fetched_at=ts, payload=json.dumps(feats, separators=(",", ":"))))
            s.commit()
    except Exception:
        log.warning("could not persist recreation site cell %s", key, exc_info=True)


async def _refresh_cell(key: str, tz: int, ix: int, iy: int) -> list:
    feats = await _fetch_cell(tz, ix, iy)
    now = time.time()
    _mem[key] = (now, feats)
    _db_store(key, now, feats)
    return feats


def _ttl(feats: list) -> float:
    return CELL_TTL_S if feats else EMPTY_CELL_TTL_S


async def get_cell(tz: int, ix: int, iy: int) -> tuple[list, bool]:
    """(features, fresh). A cell younger than its TTL is returned as-is (30 days; an empty cell only 1 day, so a
    glitchy empty answer can't hide a site for a month); an older one is refetched, and if the service is down the
    old copy is served instead (fresh=False). Raises RecSitesUnavailable when there is nothing to fall back on."""
    key = f"recsites:{CACHE_SCHEMA}:{tz}:{ix}:{iy}"
    entry = _mem.get(key) or _db_load(key)
    if entry:
        _mem[key] = entry
        if time.time() - entry[0] < _ttl(entry[1]):
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
    except RecSitesUnavailable:
        if entry:
            log.warning("USFS unavailable; serving stale recreation site cell %s", key)
            return entry[1], False
        raise


def prune_cache(max_age_s: float = PRUNE_AFTER_S) -> int:
    """Drop cells nobody has needed for a long time (from memory and Postgres); returns how many rows went."""
    cutoff = time.time() - max_age_s
    for k in [k for k, (ts, _f) in _mem.items() if ts < cutoff]:
        del _mem[k]
    try:
        with Session(engine) as s:
            n = s.execute(delete(RecSiteCell).where(RecSiteCell.fetched_at < cutoff)).rowcount or 0
            s.commit()
        return n
    except Exception:
        log.debug("recreation site cache prune failed", exc_info=True)
        return 0


class BadBounds(ValueError):
    """The requested map area is invalid, too zoomed out, or too large."""


async def recreation_sites(south: float, west: float, north: float, east: float, zoom: int) -> dict:
    """GeoJSON FeatureCollection of recreation sites intersecting the box, plus `attribution` and `partial`."""
    if not (-85 <= south < north <= 85 and -180 <= west < east <= 180):
        raise BadBounds("invalid bounds")
    if zoom < MIN_ZOOM:
        raise BadBounds(f"zoom in to at least level {MIN_ZOOM} to load recreation sites")
    tz = tile_zoom(zoom)
    cells = cells_for_bbox(south, west, north, east, tz)
    if len(cells) > MAX_CELLS:
        raise BadBounds("map area is too large; zoom in")

    results = await asyncio.gather(*(get_cell(tz, x, y) for x, y in cells), return_exceptions=True)
    merged: dict = {}
    partial = False
    failures = []
    for res in results:
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
        raise failures[0] if isinstance(failures[0], RecSitesUnavailable) else RecSitesUnavailable(str(failures[0]))
    return {"type": "FeatureCollection", "features": features, "attribution": ATTRIBUTION, "partial": partial}
