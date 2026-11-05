"""Import OpenStreetMap trails (foot, horse, two-track) from Geofabrik's US state extracts.

Each state that contains a region is downloaded (`{state}-latest.osm.pbf`, ~100-350 MB), read once with pyosmium
keeping only `highway=path|footway|bridleway|track` ways, simplified, bucketed into a fixed 0.25° grid and stored in
`osm_trail_cells`. The map then reads those cells (app.osmtrails.service) and never waits on OpenStreetMap.

The "osm_trails" job (app.jobs) runs daily and re-imports a state once its data is IMPORT_EVERY_S old, imports states
for new regions, and drops states no region uses any more. Creating or moving a region starts it right away
(`kick`). The server is short on memory, so a state is read in three passes that keep only the trail nodes'
locations (see parse_extract), in a separate short-lived process (its memory is returned when it exits). A state's
cells are replaced in one transaction only after its whole import succeeded."""
from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import sys
import tempfile
import time
from typing import Optional

import httpx
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.config import APP_VERSION
from app.core.database import engine
from app.osmtrails.models import OsmExtract, OsmTrailCell

log = logging.getLogger(__name__)

INDEX_URL = "https://download.geofabrik.de/index-v1.json"
INDEX_TTL_S = 24 * 3600
IMPORT_EVERY_S = 30 * 24 * 3600
USER_AGENT = f"AmbushIQ/{APP_VERSION or 'dev'} (self-hosted hunting map)"
WORK_DIR = os.environ.get("OSM_WORK_DIR") or tempfile.gettempdir()
GRID_DEG = 0.25
SIMPLIFY_M = 1.0

KEEP_HIGHWAY = {"path", "footway", "bridleway", "track"}
SKIP_FOOTWAY = {"sidewalk", "crossing", "access_aisle", "traffic_island"}
_UNNAMED = {"foot": "Unnamed trail", "horse": "Unnamed horse trail", "track": "Unnamed track"}
UNNAMED_NAMES = set(_UNNAMED.values())


# ---------- features ----------

def classify(tags: dict) -> Optional[str]:
    """'foot' | 'horse' | 'track', or None for a way that isn't a trail we show (sidewalks, crossings, areas)."""
    hw = tags.get("highway")
    if hw not in KEEP_HIGHWAY or tags.get("area") == "yes":
        return None
    if hw == "footway" and tags.get("footway") in SKIP_FOOTWAY:
        return None
    if hw == "track":
        return "track"
    if hw == "bridleway" or (hw == "path" and tags.get("horse") == "designated"):
        return "horse"
    return "foot"


def normalize_osm_way(way_id: int, tags: dict, coords: list) -> Optional[dict]:
    """GeoJSON feature for one OSM way ([lon, lat] coords, already simplified), or None if it isn't shown."""
    kind = classify(tags)
    if kind is None or len(coords) < 2:
        return None
    access = (tags.get("access") or "").strip() or None
    return {
        "type": "Feature", "id": f"osm-{way_id}",
        "geometry": {"type": "LineString", "coordinates": coords},
        "properties": {
            "name": (tags.get("name") or "").strip() or _UNNAMED[kind], "kind": kind, "access": access,
            "surface": (tags.get("surface") or "").strip() or None, "source": "OpenStreetMap",
        },
    }


def cell_of(lon: float, lat: float) -> tuple[int, int]:
    return math.floor((lon + 180) / GRID_DEG), math.floor((lat + 90) / GRID_DEG)


def cells_touched(coords: list) -> list[tuple[int, int]]:
    """Every grid cell the line's bounding box touches (a slight over-count is harmless: reads dedupe by id)."""
    xs = [c[0] for c in coords]
    ys = [c[1] for c in coords]
    x0, y0 = cell_of(min(xs), min(ys))
    x1, y1 = cell_of(max(xs), max(ys))
    return [(x, y) for x in range(x0, x1 + 1) for y in range(y0, y1 + 1)]


TAG_KEYS = ("highway", "name", "access", "surface", "horse", "footway", "area")


def parse_extract(path: str) -> tuple[dict, int]:
    """Read a .osm.pbf (or .osm XML) file: {(ix, iy): [feature JSON text, ...]} and the number of trail ways kept.

    Built for a small-memory server: rather than an index of every node in the state (~40 million for Illinois),
    it reads the file three times:
      1. trail ways only: collect the node ids they use (a compact int64 array, ~5 million for Illinois);
      2. nodes: store locations for just those ids, in compact float arrays found by walking the sorted ids
         alongside the file (extracts list nodes in id order; bisect covers a file that doesn't);
      3. trail ways again: look up their coordinates, simplify, and keep each feature as JSON text.
    Peak memory stays in the low hundreds of MB even for Illinois, at the cost of reading the file three times."""
    import bisect
    from array import array

    import numpy as np
    import osmium   # imported here so the app (and its tests) load without it until an import actually runs

    from app.roads.service import simplify_line_coords

    def trail_ways():
        for w in osmium.FileProcessor(path, osmium.osm.WAY).with_filter(osmium.filter.KeyFilter("highway")):
            if w.tags.get("highway") not in KEEP_HIGHWAY:
                continue
            tags = {k: w.tags.get(k) for k in TAG_KEYS if k in w.tags}
            if classify(tags) is not None:
                yield w, tags

    # 1. which nodes
    refs = array("q")
    for w, _tags in trail_ways():
        refs.extend(n.ref for n in w.nodes)
    ids = array("q", np.unique(np.frombuffer(refs, dtype=np.int64)).tobytes()) if refs else array("q")
    del refs
    n = len(ids)
    lon, lat = array("d", bytes(8 * n)), array("d", bytes(8 * n))
    found = array("b", bytes(n))

    # 2. their locations
    class Nodes(osmium.SimpleHandler):
        j = 0
        last = -1
        in_order = True

        def node(self, node):
            i = node.id
            if self.in_order and i < self.last:
                self.in_order = False               # not in id order: binary search from here on
            if self.in_order:
                k = self.j
                while k < n and ids[k] < i:
                    k += 1
                self.j = k
            else:
                k = bisect.bisect_left(ids, i)
            self.last = i
            if k < n and ids[k] == i and node.location.valid():
                lon[k], lat[k], found[k] = node.location.lon, node.location.lat, 1

    if n:
        Nodes().apply_file(path)
    ids_np = np.frombuffer(ids, dtype=np.int64)
    lon_np, lat_np, found_np = (np.frombuffer(a, dtype=t) for a, t in ((lon, np.float64), (lat, np.float64),
                                                                         (found, np.int8)))

    # 3. the lines
    cells: dict = {}
    kept = 0
    for w, tags in (trail_ways() if n else ()):
        k = np.searchsorted(ids_np, [nd.ref for nd in w.nodes])
        if not found_np[k].all():
            continue   # a node outside the extract: skip the way rather than draw it wrong
        raw = np.column_stack((lon_np[k], lat_np[k])).round(7).tolist()
        coords = simplify_line_coords(raw, SIMPLIFY_M / 111_320.0, raw[0][1])
        feat = normalize_osm_way(w.id, tags, coords) if coords else None
        if feat is None:
            continue
        kept += 1
        text = json.dumps(feat, separators=(",", ":"))
        for c in cells_touched(coords):
            cells.setdefault(c, []).append(text)
    return cells, kept


def run_import_process(extract_id: str, pbf: str) -> int:
    """Parse and store one state (in the separate process started by import_extract)."""
    cells, kept = parse_extract(pbf)
    store_cells(extract_id, cells, kept)
    return kept


# ---------- which states ----------

_index: dict = {"at": 0.0, "features": None}


async def fetch_index() -> list[dict]:
    """Geofabrik's extract list (with boundary polygons), cached for a day."""
    if _index["features"] is not None and time.time() - _index["at"] < INDEX_TTL_S:
        return _index["features"]
    async with httpx.AsyncClient(headers={"User-Agent": USER_AGENT}) as client:
        r = await client.get(INDEX_URL, timeout=60)
    r.raise_for_status()
    _index.update(at=time.time(), features=r.json()["features"])
    return _index["features"]


def _in_ring(lon: float, lat: float, ring: list) -> bool:
    inside = False
    for (x1, y1), (x2, y2) in zip(ring, ring[1:] + ring[:1]):
        if (y1 > lat) != (y2 > lat) and lon < x1 + (lat - y1) * (x2 - x1) / (y2 - y1):
            inside = not inside
    return inside


def point_in_geometry(geom: dict, lon: float, lat: float) -> bool:
    polys = [geom["coordinates"]] if geom["type"] == "Polygon" else geom["coordinates"]
    return any(_in_ring(lon, lat, poly[0]) and not any(_in_ring(lon, lat, h) for h in poly[1:]) for poly in polys)


def _bbox_area(geom: dict) -> float:
    pts = [p for poly in ([geom["coordinates"]] if geom["type"] == "Polygon" else geom["coordinates"])
           for p in poly[0]]
    return (max(p[0] for p in pts) - min(p[0] for p in pts)) * (max(p[1] for p in pts) - min(p[1] for p in pts))


def pick_extract(index: list[dict], lat: float, lon: float) -> Optional[dict]:
    """The smallest US extract (a state, or a part of one such as us/california/norcal) containing the point."""
    hits = [f for f in index if f["properties"].get("id", "").startswith("us/") and f.get("geometry")
            and f["properties"].get("urls", {}).get("pbf") and point_in_geometry(f["geometry"], lon, lat)]
    return min(hits, key=lambda f: _bbox_area(f["geometry"])) if hits else None


def _region_points() -> list[tuple[float, float]]:
    from app.regions.models import Region
    with Session(engine) as s:
        return [(r.lat, r.lon) for r in s.scalars(select(Region)).all()]


def needed_extracts(index: list[dict], points: list[tuple[float, float]]) -> dict[str, dict]:
    out = {}
    for lat, lon in points:
        f = pick_extract(index, lat, lon)
        if f:
            out[f["properties"]["id"]] = f
    return out


# ---------- importing one state ----------

async def _download(url: str, dest: str) -> None:
    async with httpx.AsyncClient(headers={"User-Agent": USER_AGENT}, follow_redirects=True) as client:
        async with client.stream("GET", url, timeout=httpx.Timeout(60, read=300)) as r:
            r.raise_for_status()
            with open(dest, "wb") as fh:
                async for chunk in r.aiter_bytes(1 << 20):
                    fh.write(chunk)


def _set_status(extract_id: str, **fields) -> None:
    with Session(engine) as s:
        row = s.get(OsmExtract, extract_id)
        if row:
            for k, v in fields.items():
                setattr(row, k, v)
            s.commit()


def store_cells(extract_id: str, cells: dict, kept: int) -> None:
    """Replace the state's cells in one transaction (a failure leaves the previous import in place)."""
    with Session(engine) as s:
        s.execute(delete(OsmTrailCell).where(OsmTrailCell.extract_id == extract_id))
        for (ix, iy), texts in cells.items():
            s.add(OsmTrailCell(key=f"osm:{extract_id}:{ix}:{iy}", extract_id=extract_id,
                               payload="[" + ",".join(texts) + "]"))
        row = s.get(OsmExtract, extract_id)
        if row:
            row.status, row.imported_at, row.way_count, row.error = "ok", time.time(), kept, None
        s.commit()


async def _import_in_subprocess(extract_id: str, pbf: str) -> int:
    """Run the parse + store in a short-lived Python process: its memory goes back to the system as soon as it
    exits, instead of staying with the long-running app process."""
    backend_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    proc = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "app.osmtrails.importer", extract_id, pbf, cwd=backend_dir,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    out, err = await proc.communicate()
    if proc.returncode != 0:
        tail = err.decode(errors="replace").strip().splitlines()[-1:] or ["no output"]
        raise RuntimeError(f"import process failed ({proc.returncode}): {tail[0]}")
    return int(out.decode().strip().splitlines()[-1])


async def import_extract(extract_id: str, pbf_url: str) -> int:
    """Download, read and store one state; returns the number of trail ways kept."""
    _set_status(extract_id, status="importing", error=None)
    fd, pbf = tempfile.mkstemp(prefix="osm-", suffix=".osm.pbf", dir=WORK_DIR)
    os.close(fd)
    try:
        t0 = time.time()
        await _download(pbf_url, pbf)
        size_mb = os.path.getsize(pbf) / 1e6
        kept = await _import_in_subprocess(extract_id, pbf)
        log.info("osm trails: %s imported, %d ways (%.0f MB, %.0f s)", extract_id, kept, size_mb, time.time() - t0)
        from app.osmtrails import service
        service.invalidate()
        return kept
    finally:
        try:
            os.remove(pbf)
        except OSError:
            pass


# ---------- the job ----------

def _sync_rows(needed: dict[str, dict], tried: frozenset = frozenset()) -> list[tuple[str, str]]:
    """Add rows for newly needed states; drop states (and their cells) no region uses. Returns the states due for
    an import, as (extract id, pbf url), leaving out (and not re-queueing) those already `tried` in this run."""
    now = time.time()
    with Session(engine) as s:
        rows = {r.extract_id: r for r in s.scalars(select(OsmExtract)).all()}
        for eid in set(rows) - set(needed):
            s.execute(delete(OsmTrailCell).where(OsmTrailCell.extract_id == eid))
            s.delete(rows.pop(eid))
        for eid, f in needed.items():
            if eid not in rows:
                rows[eid] = OsmExtract(extract_id=eid, pbf_url=f["properties"]["urls"]["pbf"],
                                       polygon_json=json.dumps(f["geometry"]), status="queued", way_count=0)
                s.add(rows[eid])
            else:
                rows[eid].pbf_url = f["properties"]["urls"]["pbf"]
                rows[eid].polygon_json = json.dumps(f["geometry"])
        due = [r for r in rows.values() if r.extract_id not in tried and
               (r.status != "ok" or not r.imported_at or now - r.imported_at > IMPORT_EVERY_S)]
        for r in due:
            if r.status != "importing":
                r.status = "queued"
        out = [(r.extract_id, r.pbf_url) for r in due]
        s.commit()
        return out


async def osm_trails_job(progress) -> str:
    """Job: import (or refresh) the state extracts the regions need. Loops until no state is due, so a region added
    while it runs is picked up in the same run."""
    imported, done_ids = [], set()
    while True:
        index = await fetch_index()
        due = await asyncio.to_thread(_sync_rows, needed_extracts(index, _region_points()), frozenset(done_ids))
        if not due:
            break
        progress.set_total(len(done_ids) + len(due))
        for extract_id, pbf_url in due:
            done_ids.add(extract_id)
            progress.begin(extract_id)
            try:
                kept = await import_extract(extract_id, pbf_url)
                imported.append(f"{extract_id.split('/')[-1]} ({kept:,} trails)")
                progress.step()
            except Exception as e:
                log.exception("osm trails: import of %s failed", extract_id)
                _set_status(extract_id, status="failed", error=(str(e) or type(e).__name__)[:500])
                progress.fail(extract_id, str(e) or type(e).__name__)
    return ("imported " + ", ".join(imported)) if imported else "up to date"


async def kick() -> None:
    """Start the job in the background (a region was created or moved); a run already in progress picks it up."""
    from app.jobs.service import JobBusy, start_background
    try:
        start_background("osm_trails")
    except JobBusy:
        pass
    except Exception:
        log.warning("could not start the OSM trails import", exc_info=True)


if __name__ == "__main__":
    # python -m app.osmtrails.importer <extract id> <pbf path>: one state's parse + store (see _import_in_subprocess)
    logging.basicConfig(level=logging.INFO)
    print(run_import_process(sys.argv[1], sys.argv[2]))
