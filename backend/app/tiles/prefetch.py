"""Overnight prefetch of terrain overlay tiles (contours, hillshade) into the server's tile cache.

Zoom-18 contour and hillshade blocks are slow, heavy USGS 3DEP renders, and 3DEP answers bursts of 502/504 when it's
busy, so an offline download that has to fetch them live goes slowly and leaves gaps. Instead the server fetches
them ahead of time, at night, a couple of blocks at a time (background priority in service._UpstreamLimiter); an
offline download of a prepared area is then just disk reads.

What gets prefetched each night:
- areas queued from the offline-maps panel (TilePrefetchArea rows), oldest first, until each is fully cached;
- every region's property box (regions.terrain.property_box: the stands plus a margin, 5-8 km across).
Already-cached blocks cost one stat each and are touched so the 180-day prune keeps them; a run stops at its
deadline and the next night carries on where it left off."""
from __future__ import annotations

import asyncio
import logging
import math
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Iterable, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import engine
from app.tiles import service
from app.tiles.models import TilePrefetchArea

log = logging.getLogger(__name__)

PREFETCH_LAYERS = ("contours", "hillshade")   # the slow 3DEP ones; woods & water (NLCD, z<=15) is cheap live
MAX_BLOCKS = 20000                             # per request (~320k tiles); far beyond any sane offline area
RUN_FOR_S = 6 * 3600                           # scheduled at 06:00 UTC (midnight Central), so done by noon UTC
EST_S_PER_BLOCK = 8.0                          # ~15 s per heavy 3DEP render, two at a time
SAVE_EVERY = 25                                # blocks between progress writes
KEEP_READY_DAYS = 30                           # finished requests stay listed in the panel this long

Bounds = tuple[float, float, float, float]     # north, south, west, east (degrees)
Block = tuple[str, int, int, int]              # layer, z, x0, y0


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _tile_xy(lat: float, lon: float, z: int) -> tuple[int, int]:
    """Slippy-map tile holding lat/lon at zoom z."""
    lat = max(-85.0511, min(85.0511, lat))
    n = 2 ** z
    x = (lon + 180.0) / 360.0 * n
    r = math.radians(lat)
    y = (1.0 - math.log(math.tan(r) + 1.0 / math.cos(r)) / math.pi) / 2.0 * n
    return min(n - 1, max(0, int(x))), min(n - 1, max(0, int(y)))


def blocks_for(bounds: Bounds, layer: str, min_zoom: int, max_zoom: int) -> list[Block]:
    """Every upstream block (META x META tiles) of `layer` covering `bounds`, at the zooms the layer is rendered
    at within min_zoom..max_zoom, lowest zoom first."""
    north, south, west, east = bounds
    lo, hi = service.LAYERS[layer]
    out: list[Block] = []
    for z in range(max(lo, min_zoom), min(hi, max_zoom) + 1):
        x_min, y_min = _tile_xy(north, west, z)
        x_max, y_max = _tile_xy(south, east, z)
        bx0, by0 = service.block_of(x_min, y_min)
        for y0 in range(by0, y_max + 1, service.META):
            for x0 in range(bx0, x_max + 1, service.META):
                out.append((layer, z, x0, y0))
    return out


def plan(bounds: Bounds, layers: Iterable[str], min_zoom: int, max_zoom: int) -> list[Block]:
    blocks: list[Block] = []
    for layer in layers:
        if layer in PREFETCH_LAYERS:
            blocks += blocks_for(bounds, layer, min_zoom, max_zoom)
    return blocks


def valid_bounds(bounds: Bounds) -> bool:
    north, south, west, east = bounds
    return -85.0511 <= south < north <= 85.0511 and -180.0 <= west < east <= 180.0


def check(bounds: Bounds, layers: Iterable[str], min_zoom: int, max_zoom: int) -> dict:
    """How much of an area's terrain tiles the server already has (file stats only, no upstream calls).
    Raises ValueError for an area too big to prefetch."""
    blocks = plan(bounds, layers, min_zoom, max_zoom)
    if len(blocks) > MAX_BLOCKS:
        raise ValueError(f"area too large ({len(blocks):,} blocks, max {MAX_BLOCKS:,})")
    missing = sum(1 for b in blocks if not service.block_cached(*b))
    return {
        "ready": missing == 0, "total_blocks": len(blocks), "missing_blocks": missing,
        "est_minutes": math.ceil(missing * EST_S_PER_BLOCK / 60) if missing else 0,
    }


@dataclass
class Tally:
    cached: int = 0          # already on disk (touched)
    fetched: int = 0
    failed: int = 0
    timed_out: bool = False
    failures: list[str] = field(default_factory=list)

    @property
    def done(self) -> int:
        return self.cached + self.fetched


async def prefetch_blocks(blocks: list[Block], deadline: float, on_progress=None) -> Tally:
    """Makes sure every block is in the tile cache: touches cached ones, fetches the rest at background priority
    (service.BACKGROUND_MAX at a time). Stops starting new blocks once `deadline` (time.monotonic) passes."""
    tally = Tally()
    queue = list(blocks)
    idx = 0

    async def worker():
        nonlocal idx
        while idx < len(queue):
            if time.monotonic() >= deadline:
                tally.timed_out = True
                return
            layer, z, x0, y0 = queue[idx]
            idx += 1
            if await asyncio.to_thread(service.block_cached, layer, z, x0, y0):
                await asyncio.to_thread(service.touch_block, layer, z, x0, y0)
                tally.cached += 1
            else:
                try:
                    await service.get_tile(layer, z, x0, y0, background=True)
                    tally.fetched += 1
                except (service.UpstreamError, service.TileAbandoned) as e:
                    tally.failed += 1
                    if len(tally.failures) < 5:
                        tally.failures.append(f"{layer}/{z}/{x0}/{y0}: {e}")
            if on_progress is not None:
                on_progress(tally)

    await asyncio.gather(*(worker() for _ in range(service.BACKGROUND_MAX)))
    return tally


# ---------- requests from the offline-maps panel ----------

def _covers(a: TilePrefetchArea, bounds: Bounds, layers: set[str], min_zoom: int, max_zoom: int) -> bool:
    north, south, west, east = bounds
    return (a.north >= north and a.south <= south and a.west <= west and a.east >= east
            and layers <= set(a.layers.split(",")) and a.min_zoom <= min_zoom and a.max_zoom >= max_zoom)


def queue_area(bounds: Bounds, layers: Iterable[str], min_zoom: int, max_zoom: int,
               region_id: Optional[int], label: str) -> dict:
    """Queues an area for tonight's prefetch; an unfinished request that already covers it is reused."""
    wanted = {l for l in layers if l in PREFETCH_LAYERS}
    if not wanted:
        raise ValueError("no terrain layers to prepare")
    total = len(plan(bounds, wanted, min_zoom, max_zoom))
    if total > MAX_BLOCKS:
        raise ValueError(f"area too large ({total:,} blocks, max {MAX_BLOCKS:,})")
    with Session(engine) as s:
        for a in s.scalars(select(TilePrefetchArea).where(TilePrefetchArea.status.in_(("queued", "running", "partial")))):
            if _covers(a, bounds, wanted, min_zoom, max_zoom):
                return a.to_dict()
        north, south, west, east = bounds
        a = TilePrefetchArea(region_id=region_id, label=(label or "Offline area")[:120], north=north, south=south,
                             west=west, east=east, layers=",".join(sorted(wanted)), min_zoom=min_zoom,
                             max_zoom=max_zoom, status="queued", blocks_total=total, created_at=_now())
        s.add(a)
        s.commit()
        return a.to_dict()


def list_areas(region_id: Optional[int]) -> list[dict]:
    """The requests for a region (and ones with no region), newest first."""
    with Session(engine) as s:
        q = select(TilePrefetchArea).order_by(TilePrefetchArea.id.desc())
        rows = s.scalars(q).all()
        return [a.to_dict() for a in rows if region_id is None or a.region_id in (None, region_id)]


def _save(area_id: int, **fields) -> None:
    with Session(engine) as s:
        a = s.get(TilePrefetchArea, area_id)
        if a is not None:
            for k, v in fields.items():
                setattr(a, k, v)
            s.commit()


def _prune_old_requests() -> int:
    cutoff = datetime.now(timezone.utc).timestamp() - KEEP_READY_DAYS * 86400
    n = 0
    with Session(engine) as s:
        for a in s.scalars(select(TilePrefetchArea).where(TilePrefetchArea.status == "ready")).all():
            try:
                finished = datetime.fromisoformat(a.finished_at).timestamp()
            except ValueError:
                continue
            if finished < cutoff:
                s.delete(a)
                n += 1
        s.commit()
    return n


# ---------- the nightly job ----------

def _region_boxes() -> list[tuple[str, Bounds]]:
    from app.regions import terrain as rt
    from app.regions.models import Region
    with Session(engine) as s:
        regions = [(r.id, r.name) for r in s.scalars(select(Region).order_by(Region.id)).all()]
    out = []
    for rid, name in regions:
        g = rt._box_grid(*rt.property_box(rid))
        out.append((name, (g.north, g.south, g.west, g.east)))
    return out


async def prefetch_tiles_job(progress, run_for_s: float = RUN_FOR_S) -> str:
    """Scheduler job (nightly): queued offline-panel areas first, oldest first, then each region's property box.
    Contours z13-18 and hillshade z8-18 for region boxes; queued areas use the zooms they were asked for."""
    deadline = time.monotonic() + run_for_s
    _prune_old_requests()
    with Session(engine) as s:
        requests = [(a.id, a.label, (a.north, a.south, a.west, a.east), a.layers.split(","), a.min_zoom, a.max_zoom)
                    for a in s.scalars(select(TilePrefetchArea)
                                       .where(TilePrefetchArea.status.in_(("queued", "running", "partial")))
                                       .order_by(TilePrefetchArea.id)).all()]
    boxes = await asyncio.to_thread(_region_boxes)
    progress.set_total(len(requests) + len(boxes))
    fetched = cached = failed = 0
    out_of_time = False

    for area_id, label, bounds, layers, mn, mx in requests:
        if time.monotonic() >= deadline:
            out_of_time = True
            break
        progress.begin(label)
        blocks = plan(bounds, layers, mn, mx)
        _save(area_id, status="running", blocks_total=len(blocks), blocks_done=0, blocks_failed=0)
        last = [0]

        def on_progress(t: Tally, area_id=area_id, last=last):
            if t.done + t.failed - last[0] >= SAVE_EVERY:
                last[0] = t.done + t.failed
                _save(area_id, blocks_done=t.done, blocks_failed=t.failed)

        t = await prefetch_blocks(blocks, deadline, on_progress)
        complete = not t.failed and not t.timed_out
        _save(area_id, status="ready" if complete else "partial", blocks_done=t.done, blocks_failed=t.failed,
              finished_at=_now() if complete else "")
        fetched, cached, failed = fetched + t.fetched, cached + t.cached, failed + t.failed
        if complete:
            progress.step()
        else:
            progress.fail(label, f"{t.failed:,} blocks failed" + ("; out of time, continues next run" if t.timed_out else "")
                          + (f" (e.g. {t.failures[0]})" if t.failures else ""))
        out_of_time = out_of_time or t.timed_out

    for name, bounds in boxes:
        if out_of_time or time.monotonic() >= deadline:
            out_of_time = True
            break
        progress.begin(f"{name} (region)")
        t = await prefetch_blocks(plan(bounds, PREFETCH_LAYERS, 0, 99), deadline)
        fetched, cached, failed = fetched + t.fetched, cached + t.cached, failed + t.failed
        if t.failed or t.timed_out:
            progress.fail(name, f"{t.failed:,} blocks failed" + ("; out of time, continues next run" if t.timed_out else "")
                          + (f" (e.g. {t.failures[0]})" if t.failures else ""))
            out_of_time = out_of_time or t.timed_out
        else:
            progress.step()

    msg = f"fetched {fetched:,} blocks, {cached:,} already cached"
    if failed:
        msg += f", {failed:,} failed (retried next run)"
    if out_of_time:
        msg += "; stopped at the time limit, continues next run"
    log.info("tile prefetch: %s", msg)
    return msg
