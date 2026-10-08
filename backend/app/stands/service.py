"""Business logic for stands: terrain analysis + storage, shared by the per-stand
endpoint and the region-wide re-analysis job."""
from __future__ import annotations

import asyncio
import json
import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import engine
from app.stands import terrain as terrain_mod
from app.stands.models import Stand

log = logging.getLogger(__name__)
_auto_running: set[int] = set()      # stand ids with an automatic analysis in flight
_auto_tasks: set[asyncio.Task] = set()   # strong refs so running tasks aren't garbage-collected
_auto_lock = asyncio.Lock()          # one automatic analysis at a time, inside the elevation services' rate limits


async def analyze_and_store(stand_id: int, progress_callback=None) -> dict:
    """Fetch + analyze terrain at the stand's location and save it on the stand.
    Returns the updated stand dict."""
    with Session(engine) as s:
        st = s.get(Stand, stand_id)
        if not st:
            raise ValueError(f"stand {stand_id} not found")
        lat, lon = st.lat, st.lon
    terrain = await terrain_mod.fetch_terrain(lat, lon, progress_callback=progress_callback)
    with Session(engine) as s:
        st = s.get(Stand, stand_id)
        st.terrain_json = json.dumps(terrain)
        st.downhill_deg = None if terrain.get("flat") else terrain["downhill_deg"]
        s.commit()
        s.refresh(st)
        return st.to_dict()


def auto_analyze_if_outside(stand_id: int, region_id: int, *, only_missing: bool = False) -> bool:
    """Start the stand's own terrain analysis in the background when the property grid can't supply
    its terrain (outside the grid, or the grid is the coarse Open-Meteo one). `only_missing` skips
    stands that already have an analysis of their own. Must be called on the event loop; returns
    whether an analysis was started. Never raises."""
    from app.regions import drainage   # regions.drainage imports stands.terrain: import lazily
    try:
        with Session(engine) as s:
            st = s.get(Stand, stand_id)
            if not st or st.region_id != region_id:
                return False
            lat, lon, has_own = st.lat, st.lon, bool(st.terrain_json)
        if stand_id in _auto_running or (only_missing and has_own) or drainage.covered(region_id, lat, lon):
            return False
        _auto_running.add(stand_id)

        async def run():
            try:
                async with _auto_lock:
                    await analyze_and_store(stand_id)
            except Exception:
                log.exception("automatic terrain analysis failed for stand %s", stand_id)
            finally:
                _auto_running.discard(stand_id)

        task = asyncio.get_running_loop().create_task(run())
        _auto_tasks.add(task)
        task.add_done_callback(_auto_tasks.discard)
        return True
    except Exception:
        log.exception("automatic terrain check failed for stand %s", stand_id)
        return False


def auto_analyze_uncovered(region_id: int) -> int:
    """After the property grid is (re)built: give every stand it can't cover, and that has no
    analysis of its own yet, one. Returns how many were started."""
    with Session(engine) as s:
        ids = [sid for (sid,) in s.execute(select(Stand.id).where(Stand.region_id == region_id))]
    return sum(auto_analyze_if_outside(sid, region_id, only_missing=True) for sid in ids)


async def terrain_reanalyze_job(progress, region_id: int) -> None:
    """Job: re-analyze terrain for every stand in a region (active or not), one at a time
    to stay inside the elevation services' rate limits. A failing stand is recorded and
    skipped so one bad location doesn't stop the rest."""
    with Session(engine) as s:
        stands = [(st.id, st.name) for st in s.scalars(
            select(Stand).where(Stand.region_id == region_id).order_by(Stand.name)).all()]
    progress.set_total(len(stands))
    for sid, name in stands:
        progress.begin(name)
        try:
            await analyze_and_store(sid)
            progress.step()
        except Exception as e:
            progress.fail(name, str(e) or type(e).__name__)
