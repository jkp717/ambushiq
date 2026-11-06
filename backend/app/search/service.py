"""Map search: find towns, streets, waterways, landforms and trails by name, near the map view first.

Four sources are asked at once, each with its own time limit, so one slow or failing service never holds up the rest
(the answer lists the sources that failed):
  - Esri World Geocoder (findAddressCandidates, limited to place/street/water/land/park categories, ranked toward the
    view): towns, streets, named streams, trails and parks anywhere in the US. Never cached or stored: Esri's free
    geocoding doesn't allow saving results (same as app.roads.street_name).
  - USGS GNIS place names (carto.nationalmap.gov geonames): populated places, landforms (ridges, hollows, summits),
    streams (at their mouth) and other water. GNIS can't sort by distance, so it's asked for the area around the view
    (SEARCH_RADIUS_MI) and, only if nothing matches there, for the whole country.
  - US Forest Service MVUM trails and roads around the view (by name, or by road/trail number), with their lines.
  - The OpenStreetMap trail name index (osm_trail_names, built at each state import) for foot/horse trails and tracks.

Measured: Esri ~0.25 s, GNIS 0.4-0.9 s per request, nationwide or local alike; the index is a local query.
Results are ranked by distance from the view, with a penalty for a weaker name match, deduped and capped."""
from __future__ import annotations

import asyncio
import json
import logging
import math
import re
import time
from collections import OrderedDict
from typing import Optional

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import APP_VERSION
from app.core.database import engine

log = logging.getLogger(__name__)

ESRI_URL = "https://geocode.arcgis.com/arcgis/rest/services/World/GeocodeServer/findAddressCandidates"
ESRI_CATEGORIES = "Populated Place,Street Name,Water Features,Land Features,Parks and Outdoors"
GNIS_BASE = "https://carto.nationalmap.gov/arcgis/rest/services/geonames/MapServer"
GNIS_LAYERS = {3: "town", 5: "landform", 6: "water", 7: "water"}   # populated places, landforms, stream mouths, other water
USER_AGENT = f"AmbushIQ/{APP_VERSION or 'dev'} (self-hosted hunting map)"

MIN_QUERY = 3
MAX_RESULTS = 15
SEARCH_RADIUS_MI = 75
REQUEST_TIMEOUT_S = 6
SOURCE_TIMEOUT_S = 8          # a source still waiting after this is dropped from this answer
GNIS_PER_LAYER = 40
MVUM_MAX = 300
OSM_MAX = 200
DEDUPE_MI = 2.0
# Added to the distance (miles) when ranking, by how well the name matches: exact, starts with, a word starts with,
# anywhere. A local "Bear Creek Rd" (prefix) still beats an exact "Bear Creek" across the country.
MATCH_PENALTY_MI = (0, 5, 15, 40)
ESRI_MIN_SCORE = 80          # below this Esri is guessing (e.g. "Zzyzx Hollow" -> towns named "Hollow", 78)
ESRI_SURE_SCORE = 99         # Esri is sure of the name even if spelled differently ("Bear Crk" -> "Bear Creek")
CACHE_TTL_S = 300
CACHE_MAX = 200

_ESRI_WATER = {"stream", "river", "lake", "reservoir", "pond", "spring", "waterfall", "bay", "canal", "creek", "swamp",
               "marsh", "wetland", "dam", "harbor", "inlet", "channel", "rapids", "sound", "estuary", "lagoon", "basin",
               "water feature"}
_ESRI_LAND = {"mountain", "summit", "peak", "ridge", "valley", "hill", "gap", "canyon", "cliff", "bluff", "plain",
              "plateau", "island", "cape", "cave", "flat", "bench", "arch", "rock", "range", "point", "land feature",
              "mountain range", "hollow"}
_ESRI_PARK = {"park", "campground", "forest", "wildlife area", "nature reserve", "national park", "state park",
              "recreation area", "picnic area", "trailhead", "hunting area"}


class BadQuery(ValueError):
    """The query is too short or the point is invalid."""


# ---------- query text ----------

def clean_query(q: str) -> str:
    """Letters, digits, spaces and . ' & - only (no SQL LIKE wildcards), single-spaced."""
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s.'&-]|_", " ", q or "")).strip()


def sql_like_text(q: str) -> str:
    """`q` (already cleaned) upper-cased, with quotes doubled, for an ArcGIS `UPPER(field) LIKE '%...%'` clause."""
    return q.upper().replace("'", "''")


_ROUTE_PREFIX = re.compile(r"^(?:NFSR|FSR|FS|FR|NF|FOREST(?:\s+SERVICE)?)(?:\s*(?:RD|ROAD|TR|TRAIL|ROUTE))?\.?\s*",
                           re.IGNORECASE)


def route_number(q: str) -> Optional[str]:
    """'FS Rd 1003A' / 'FR 1003' / '1003' -> '1003A' / '1003' (a Forest Service road/trail number), else None."""
    rest = _ROUTE_PREFIX.sub("", q.strip()).strip()
    return rest.upper() if re.fullmatch(r"\d+[A-Za-z0-9.]*", rest) else None


def _norm(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", name.casefold()).strip()


def match_rank(name: str, q: str) -> int:
    """0 exact, 1 starts with, 2 a word starts with, 3 anywhere / fuzzy (Esri may correct spelling)."""
    n, k = _norm(name), _norm(q)
    if not k:
        return 3
    if n == k:
        return 0
    if n.startswith(k):
        return 1
    if (" " + n).find(" " + k) >= 0:
        return 2
    return 3


# ---------- geometry helpers ----------

def distance_mi(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return 3958.8 * 2 * math.asin(min(1.0, math.sqrt(a)))


def envelope(lat: float, lon: float, miles: float) -> tuple[float, float, float, float]:
    """(west, south, east, north) of a box `miles` out from the point."""
    dlat = miles / 69.0
    dlon = miles / (69.0 * max(0.2, math.cos(math.radians(lat))))
    return lon - dlon, max(-90.0, lat - dlat), lon + dlon, min(90.0, lat + dlat)


def _line_parts(geom: dict) -> list:
    return [geom["coordinates"]] if geom["type"] == "LineString" else geom["coordinates"]


def line_bbox(geom: dict) -> list[float]:
    pts = [c for part in _line_parts(geom) for c in part]
    return [min(p[0] for p in pts), min(p[1] for p in pts), max(p[0] for p in pts), max(p[1] for p in pts)]


def nearest_point(geom: dict, lat: float, lon: float) -> tuple[float, float]:
    """(lat, lon) of the line's vertex nearest the view (distance shown in the list = how far the trail is)."""
    k = math.cos(math.radians(lat))
    best = min((c for part in _line_parts(geom) for c in part),
               key=lambda c: ((c[0] - lon) * k) ** 2 + (c[1] - lat) ** 2)
    return best[1], best[0]


# ---------- small cache (GNIS and MVUM only; Esri results may not be stored) ----------
_cache: "OrderedDict[tuple, tuple[float, list]]" = OrderedDict()


def _cache_get(key: tuple) -> Optional[list]:
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < CACHE_TTL_S:
        _cache.move_to_end(key)
        return hit[1]
    return None


def _cache_put(key: tuple, value: list) -> None:
    _cache[key] = (time.time(), value)
    _cache.move_to_end(key)
    while len(_cache) > CACHE_MAX:
        _cache.popitem(last=False)


def clear_cache() -> None:
    _cache.clear()


# ---------- sources ----------

def _esri_kind(addr_type: str, typ: str) -> str:
    t = (typ or "").casefold()
    if addr_type == "Locality":
        return "town"
    if addr_type.startswith("Street"):
        return "street"
    if t in _ESRI_WATER:
        return "water"
    if t == "trail":
        return "trail"
    if t in _ESRI_LAND:
        return "landform"
    if t in _ESRI_PARK:
        return "park"
    return "place"


def esri_result(c: dict) -> Optional[dict]:
    a, loc = c.get("attributes") or {}, c.get("location") or {}
    if loc.get("x") is None or loc.get("y") is None:
        return None
    addr_type, typ = a.get("Addr_type") or "", a.get("Type") or ""
    kind = _esri_kind(addr_type, typ)
    address = (c.get("address") or "").strip()
    county, state = a.get("Subregion") or "", a.get("Region") or ""
    place = ", ".join(x for x in (county, state) if x)
    if kind == "town":
        name = address.split(",")[0].strip()
        sub = ", ".join(x for x in ((typ if typ.casefold() == "county" else ""), place) if x) or typ
    elif kind == "street":
        parts = [p.strip() for p in address.split(",")]
        name = parts[0]
        city = parts[1] if len(parts) > 2 else ""
        sub = ", ".join(x for x in (city, state) if x) or "Street"
    else:
        name = address.split(",")[0].strip()
        sub = " · ".join(x for x in (typ, place) if x)
    out = {"kind": kind, "name": name or address, "sub": sub, "lat": loc["y"], "lon": loc["x"], "source": "esri"}
    if (c.get("score") or 0) >= ESRI_SURE_SCORE:
        out["sure"] = True
    ext = c.get("extent") or {}
    if kind == "town" and all(ext.get(k) is not None for k in ("xmin", "ymin", "xmax", "ymax")):
        out["bbox"] = [ext["xmin"], ext["ymin"], ext["xmax"], ext["ymax"]]
    return out


async def search_esri(client: httpx.AsyncClient, q: str, lat: float, lon: float) -> list[dict]:
    r = await client.get(ESRI_URL, params={
        "SingleLine": q, "location": f"{lon},{lat}", "maxLocations": 10, "category": ESRI_CATEGORIES,
        "outFields": "Type,Addr_type,City,Region,Subregion", "countryCode": "USA", "f": "json",
    }, timeout=REQUEST_TIMEOUT_S)
    r.raise_for_status()
    data = r.json()
    if data.get("error"):
        raise httpx.HTTPError(f"Esri geocoder error: {data['error']}")
    return [x for x in (esri_result(c) for c in data.get("candidates") or [] if (c.get("score") or 0) >= ESRI_MIN_SCORE)
            if x]


def gnis_result(f: dict, kind: str) -> Optional[dict]:
    p, g = f.get("properties") or {}, f.get("geometry") or {}
    coords = g.get("coordinates")
    if not coords or not p.get("gaz_name"):
        return None
    pt = coords[0] if g.get("type") == "MultiPoint" else coords
    fc, county, state = p.get("gaz_featureclass") or "", p.get("county_name") or "", p.get("state_alpha") or ""
    if fc.casefold() == "stream":
        fc = "Stream (mouth)"
    place = ", ".join(x for x in ((f"{county} Co." if county else ""), state) if x)
    return {"kind": kind, "name": p["gaz_name"], "sub": " · ".join(x for x in (fc, place) if x),
            "lat": pt[1], "lon": pt[0], "source": "gnis"}


async def _gnis_layer(client: httpx.AsyncClient, layer: int, q: str, box: Optional[tuple]) -> list[dict]:
    params = {
        "where": f"UPPER(gaz_name) LIKE '%{sql_like_text(q)}%'",
        "outFields": "gaz_name,gaz_featureclass,county_name,state_alpha", "returnGeometry": "true",
        "outSR": 4326, "resultRecordCount": GNIS_PER_LAYER, "f": "geojson",
    }
    if box:
        params.update(geometry=",".join(f"{v:.5f}" for v in box), geometryType="esriGeometryEnvelope", inSR=4326,
                      spatialRel="esriSpatialRelIntersects")
    r = await client.get(f"{GNIS_BASE}/{layer}/query", params=params, timeout=REQUEST_TIMEOUT_S)
    r.raise_for_status()
    data = r.json()
    if data.get("error"):
        raise httpx.HTTPError(f"GNIS error: {data['error']}")
    kind = GNIS_LAYERS[layer]
    return [x for x in (gnis_result(f, kind) for f in data.get("features") or []) if x]


async def search_gnis(client: httpx.AsyncClient, q: str, lat: float, lon: float) -> list[dict]:
    """Near the view first; the whole country only when nothing matches nearby."""
    key = ("gnis", _norm(q), round(lat, 1), round(lon, 1))
    hit = _cache_get(key)
    if hit is not None:
        return hit
    box = envelope(lat, lon, SEARCH_RADIUS_MI)
    found = await asyncio.gather(*(_gnis_layer(client, layer, q, box) for layer in GNIS_LAYERS))
    out = [x for lst in found for x in lst]
    if not out:
        found = await asyncio.gather(*(_gnis_layer(client, layer, q, None) for layer in GNIS_LAYERS))
        out = [x for lst in found for x in lst]
    _cache_put(key, out)
    return out


async def search_mvum(client: httpx.AsyncClient, q: str, lat: float, lon: float) -> list[dict]:
    """Forest Service trails and roads around the view whose name (or route number) matches, merged into whole
    lines, each with its geometry so the map can highlight it."""
    from app.trails import service as trails
    key = ("mvum", _norm(q), round(lat, 1), round(lon, 1))
    hit = _cache_get(key)
    if hit is not None:
        return hit
    route = route_number(q)
    where = f"UPPER(name) LIKE '%{sql_like_text(q)}%'"
    if route:
        where += f" OR UPPER(id) LIKE '{sql_like_text(route)}%'"
    box = envelope(lat, lon, SEARCH_RADIUS_MI)

    async def layer(source: str) -> list[dict]:
        url = trails.TRAILS_URL if source == "trail" else trails.ROADS_URL
        fields = (trails.TRAILS_OUT_FIELDS if source == "trail" else trails.ROADS_OUT_FIELDS) + ",id"
        r = await client.get(url, params={
            "where": where, "geometry": ",".join(f"{v:.5f}" for v in box), "geometryType": "esriGeometryEnvelope",
            "inSR": 4326, "spatialRel": "esriSpatialRelIntersects", "outFields": fields, "returnGeometry": "true",
            "outSR": 4326, "geometryPrecision": 5, "resultRecordCount": MVUM_MAX, "f": "geojson",
        }, timeout=REQUEST_TIMEOUT_S)
        r.raise_for_status()
        data = r.json()
        if data.get("error"):
            raise httpx.HTTPError(f"MVUM error: {data['error']}")
        norm = trails.normalize_trail_feature if source == "trail" else trails.normalize_road_feature
        tol = trails.tolerance_deg(14, lat)
        feats = []
        for raw in data.get("features") or []:
            f = norm(raw, tol, lat)
            if f:
                f["properties"]["route"] = ((raw.get("properties") or {}).get("id") or "").strip()
                feats.append(f)
        return feats

    found = await asyncio.gather(layer("trail"), layer("road"))
    out = []
    for f in trails.merge_trail_segments([f for lst in found for f in lst]):
        p = f["properties"]
        unnamed = p["name"] in ("Unnamed trail", "Unnamed road")
        label = "FS Rd" if p["kind"] == "road" else "FS Trail"
        name = f"{label} {p['route']}" if unnamed and p.get("route") else p["name"]
        if unnamed and not p.get("route"):
            continue
        what = "Forest Service road" if p["kind"] == "road" else "Forest Service OHV trail"
        sub = " · ".join(x for x in (what, (f"#{p['route']}" if p.get("route") and not unnamed else ""),
                                     p.get("forestname") or "") if x)
        plat, plon = nearest_point(f["geometry"], lat, lon)
        out.append({"kind": "trail", "trail_kind": "road" if p["kind"] == "road" else "trail", "name": name,
                    "sub": sub, "lat": plat, "lon": plon, "bbox": line_bbox(f["geometry"]), "source": "mvum",
                    "geometry": f["geometry"], "match_name": p["name"] if not unnamed else (p.get("route") or "")})
    _cache_put(key, out)
    return out


def search_osm(q: str, lat: float, lon: float) -> list[dict]:
    """Named OpenStreetMap trails from the states the regions are in (osm_trail_names)."""
    from app.osmtrails.models import OsmTrailName
    labels = {"foot": "Foot trail", "horse": "Horse trail", "track": "Track (two-track)"}
    with Session(engine) as s:
        rows = s.scalars(select(OsmTrailName).where(OsmTrailName.name_lc.contains(q.casefold(), autoescape=True))
                         .limit(OSM_MAX)).all()
    out = []
    for r in rows:
        out.append({"kind": "trail", "trail_kind": r.kind, "name": r.name,
                    "sub": f"{labels.get(r.kind, 'Trail')} · OpenStreetMap", "lat": r.lat, "lon": r.lon,
                    "bbox": [r.west, r.south, r.east, r.north], "source": "osm", "ref": r.id})
    return out


# ---------- one-time backfill of the OSM name index (states imported before it existed) ----------
_backfill = {"started": False}


def _maybe_backfill() -> None:
    if _backfill["started"]:
        return
    _backfill["started"] = True
    from app.osmtrails.importer import backfill_name_index

    async def run():
        try:
            await asyncio.to_thread(backfill_name_index)
        except Exception:
            log.exception("search: OSM trail name index backfill failed")
    asyncio.get_running_loop().create_task(run())


# ---------- merge, rank, dedupe ----------

def rank_and_dedupe(results: list[dict], q: str, lat: float, lon: float, limit: int = MAX_RESULTS) -> list[dict]:
    """Sort by distance + name-match penalty; drop a result with the same name as a better one within DEDUPE_MI
    (a result that can be drawn as a line wins over a point)."""
    for r in results:
        r["distance_mi"] = round(distance_mi(lat, lon, r["lat"], r["lon"]), 1)
        rank = match_rank(r.pop("match_name", None) or r["name"], q)
        if r.pop("sure", False):
            rank = min(rank, 1)
        r["_score"] = r["distance_mi"] + MATCH_PENALTY_MI[rank]
    results.sort(key=lambda r: (r["_score"], 0 if r["source"] in ("mvum", "osm") else 1))
    kept: list[dict] = []
    for r in results:
        n = _norm(r["name"])
        dup = next((i for i, k in enumerate(kept) if _norm(k["name"]) == n
                    and distance_mi(k["lat"], k["lon"], r["lat"], r["lon"]) < DEDUPE_MI), None)
        if dup is not None:
            if r["source"] in ("mvum", "osm") and kept[dup]["source"] not in ("mvum", "osm"):
                kept[dup] = r      # same place, but this one can be drawn as a line: it takes the other's spot
            continue
        if len(kept) < limit:
            kept.append(r)
    for r in kept:
        r.pop("_score", None)
    return kept


async def _guard(name: str, coro) -> tuple[str, Optional[list]]:
    try:
        return name, await asyncio.wait_for(coro, SOURCE_TIMEOUT_S)
    except Exception as e:
        log.info("search: %s failed: %s", name, e or type(e).__name__)
        return name, None


async def search(q: str, lat: float, lon: float) -> dict:
    q = clean_query(q)
    if len(q) < MIN_QUERY:
        raise BadQuery(f"type at least {MIN_QUERY} characters")
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise BadQuery("invalid point")
    _maybe_backfill()
    async with httpx.AsyncClient(headers={"User-Agent": USER_AGENT}) as client:
        answers = await asyncio.gather(
            _guard("places", search_esri(client, q, lat, lon)),
            _guard("place names", search_gnis(client, q, lat, lon)),
            _guard("Forest Service trails", search_mvum(client, q, lat, lon)),
            _guard("OpenStreetMap trails", asyncio.to_thread(search_osm, q, lat, lon)),
        )
    results = [dict(r) for _name, lst in answers if lst for r in lst]
    failed = [name for name, lst in answers if lst is None]
    for i, r in enumerate(ranked := rank_and_dedupe(results, q, lat, lon)):
        r["id"] = f"{r['source']}-{i}"
    return {"query": q, "results": ranked, "failed": failed}


def osm_trail_line(row_id: int) -> Optional[dict]:
    """The whole stretch of one indexed OSM trail as a single MultiLineString feature, or None if it's gone (the
    state was re-imported since the search)."""
    from app.osmtrails import service as osm
    from app.osmtrails.models import OsmTrailName
    with Session(engine) as s:
        row = s.get(OsmTrailName, row_id)
        if row is None:
            return None
        ids, keys = set(json.loads(row.feature_ids)), json.loads(row.cell_keys)
        name, kind = row.name, row.kind
    parts, seen = [], set()
    for cell in osm._load_cells(keys):
        for f in cell:
            if f["id"] in ids and f["id"] not in seen:
                seen.add(f["id"])
                parts.extend(_line_parts(f["geometry"]))
    if not parts:
        return None
    return {"type": "Feature", "geometry": {"type": "MultiLineString", "coordinates": parts},
            "properties": {"name": name, "kind": kind}}
