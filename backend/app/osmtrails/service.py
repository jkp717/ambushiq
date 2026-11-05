"""OpenStreetMap trails for the map: reads the cells app.osmtrails.importer stored from Geofabrik state extracts.

For a map view: load the 0.25° cells it touches (from every imported state, memory-cached until the next import),
cut the stretches another layer already draws (Forest Service MVUM trails/roads for every OSM trail, and USGS roads
too for tracks, so nothing shows twice), simplify for the zoom, merge segments into whole trails (whole-trail hover),
and keep the trails that reach into the view. The work up to the view filter is memoized per set of cells, so panning
around the same area reuses it. Cover cells come from the trails/roads layers' own caches; one that can't be loaded
just means nothing is cut there."""
from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import OrderedDict
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import engine
from app.core.line_merge import MergeMemo, merge_line_segments
from app.core.line_overlap import cut_covered
from app.osmtrails.importer import GRID_DEG, UNNAMED_NAMES, cell_of, point_in_geometry
from app.osmtrails.models import OsmExtract, OsmTrailCell

log = logging.getLogger(__name__)

ATTRIBUTION = "Trails: © OpenStreetMap contributors (ODbL), via Geofabrik"
MIN_ZOOM = 11
MAX_CELLS = 24          # a zoom-11 view spans ~12-18 of the 0.25° cells
FULL_DETAIL_ZOOM = 14   # below this, lines are simplified again for the zoom


class BadBounds(ValueError):
    """The requested map area is invalid, too zoomed out, or too large."""


# ---------- stored cells, memory-cached (bounded: the server is short on memory) until the next import ----------
MAX_CACHED_CELLS = 40   # more than a zoom-11 view needs; older cells are re-read from Postgres when needed again
_cells: "OrderedDict[str, list]" = OrderedDict()
_extracts: dict = {"at": 0.0, "rows": None}
EXTRACTS_TTL_S = 30   # import status changes while a state downloads; re-read the small table this often
_merge_memo = MergeMemo(size=6)


def invalidate() -> None:
    """Drop cached cells and status (an import just replaced a state's cells)."""
    _cells.clear()
    _extracts.update(at=0.0, rows=None)
    _merge_memo.clear()


def _extract_rows() -> list[dict]:
    if _extracts["rows"] is not None and time.time() - _extracts["at"] < EXTRACTS_TTL_S:
        return _extracts["rows"]
    with Session(engine) as s:
        rows = [{"id": r.extract_id, "status": r.status, "imported_at": r.imported_at,
                 "polygon": json.loads(r.polygon_json)} for r in s.scalars(select(OsmExtract)).all()]
    _extracts.update(at=time.time(), rows=rows)
    return rows


def _load_cells(keys: list[str]) -> list[list]:
    """Feature lists for the given cell keys (missing keys = no trails stored there), memory-cached (least recently
    used dropped past MAX_CACHED_CELLS)."""
    missing = [k for k in keys if k not in _cells]
    loaded = {}
    if missing:
        with Session(engine) as s:
            found = {r.key: r.payload for r in s.scalars(select(OsmTrailCell).where(OsmTrailCell.key.in_(missing)))}
        loaded = {k: json.loads(found[k]) if k in found else [] for k in missing}
    out = []
    for k in keys:
        lst = loaded[k] if k in loaded else _cells[k]
        _cells[k] = lst
        _cells.move_to_end(k)
        out.append(lst)
    while len(_cells) > MAX_CACHED_CELLS:
        _cells.popitem(last=False)
    return out


def _status(rows: list[dict], lat: float, lon: float, has_features: bool) -> str:
    """ok | importing | error | none (no downloaded state covers this spot: add a region in that state)."""
    if has_features:
        return "ok"
    for r in rows:
        if point_in_geometry(r["polygon"], lon, lat):
            if r["imported_at"]:
                return "ok"
            return "importing" if r["status"] in ("queued", "importing") else "error"
    return "none"


# ---------- cover lines from the other layers ----------

async def _cover_cells(south: float, west: float, north: float, east: float, zoom: int) -> tuple[list, list]:
    """(Forest Service MVUM trails+roads cell lists, USGS road cell lists) for the view, from their caches."""
    from app.roads import service as roads_service
    from app.trails import service as trails_service
    tz = trails_service.tile_zoom(zoom)
    cells = trails_service.cells_for_bbox(south, west, north, east, tz)
    fs_jobs = [trails_service.get_cell(src, tz, x, y) for x, y in cells for src in ("trail", "road")]
    road_jobs = [roads_service.get_cell(k, tz, x, y) for x, y in cells for k in roads_service._LAYER_IDS]
    res = await asyncio.gather(*fs_jobs, *road_jobs, return_exceptions=True)
    ok = [r[0] if not isinstance(r, Exception) else None for r in res]
    fs, roads = ok[:len(fs_jobs)], ok[len(fs_jobs):]
    return [c for c in fs if c is not None], [c for c in roads if c is not None]


def _unique(cell_lists: list[list]) -> list[dict]:
    return list({f["id"]: f for cell in cell_lists for f in cell}.values())


def process(features: list[dict], fs: list[dict], usgs_roads: list[dict], zoom: int) -> list[dict]:
    """Cut what other layers draw, simplify for the zoom, merge into whole trails (pure; memoized by the caller)."""
    tracks = [f for f in features if f["properties"]["kind"] == "track"]
    others = [f for f in features if f["properties"]["kind"] != "track"]
    feats = cut_covered(others, fs) + cut_covered(cut_covered(tracks, fs), usgs_roads)
    if zoom < FULL_DETAIL_ZOOM:
        from app.roads.service import simplify_line_geometry, tile_zoom, tolerance_deg
        tz = tile_zoom(zoom)
        simplified = []
        for f in feats:
            lat = f["geometry"]["coordinates"][0][1]
            geom = simplify_line_geometry(f["geometry"], tolerance_deg(tz, lat), lat)
            if geom:
                simplified.append({**f, "geometry": geom})
        feats = simplified
    return merge_line_segments(feats, group_key=lambda p: (p["kind"], p["name"], p.get("access")),
                               is_unnamed=lambda p: p["name"] in UNNAMED_NAMES)


def _bbox(geom: dict) -> tuple[float, float, float, float]:
    parts = [geom["coordinates"]] if geom["type"] == "LineString" else geom["coordinates"]
    xs = [p[0] for part in parts for p in part]
    ys = [p[1] for part in parts for p in part]
    return min(xs), min(ys), max(xs), max(ys)


async def osm_trails(south: float, west: float, north: float, east: float, zoom: int) -> dict:
    """GeoJSON FeatureCollection of OSM trails reaching into the box, plus `attribution` and `status`."""
    if not (-85 <= south < north <= 85 and -180 <= west < east <= 180):
        raise BadBounds("invalid bounds")
    if zoom < MIN_ZOOM:
        raise BadBounds(f"zoom in to at least level {MIN_ZOOM} to load trails")
    x0, y0 = cell_of(west, south)
    x1, y1 = cell_of(east, north)
    cells = [(x, y) for x in range(x0, x1 + 1) for y in range(y0, y1 + 1)]
    if len(cells) > MAX_CELLS:
        raise BadBounds("map area is too large; zoom in")

    rows = await asyncio.to_thread(_extract_rows)
    keys = [f"osm:{r['id']}:{x}:{y}" for r in rows if r["imported_at"] for x, y in cells]
    cell_lists = await asyncio.to_thread(_load_cells, keys)
    features = _unique(cell_lists)
    status = _status(rows, (south + north) / 2, (west + east) / 2, bool(features))
    if not features:
        return {"type": "FeatureCollection", "features": [], "attribution": ATTRIBUTION, "status": status}

    fs_cells, road_cells = await _cover_cells(south, west, north, east, zoom)
    fs, usgs_roads = _unique(fs_cells), _unique(road_cells)
    from app.roads.service import tile_zoom
    detail = tile_zoom(zoom) if zoom < FULL_DETAIL_ZOOM else None
    merged = await _merge_memo.get((detail, tuple(keys)), cell_lists + fs_cells + road_cells, features,
                                   lambda f: process(f, fs, usgs_roads, zoom))
    in_view = []
    for f in merged:
        bx0, by0, bx1, by1 = _bbox(f["geometry"])
        if bx1 >= west and bx0 <= east and by1 >= south and by0 <= north:
            in_view.append(f)
    return {"type": "FeatureCollection", "features": in_view, "attribution": ATTRIBUTION, "status": status}
