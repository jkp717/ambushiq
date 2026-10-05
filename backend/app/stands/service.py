"""Business logic for stands: terrain analysis + storage, shared by the per-stand
endpoint and the region-wide re-analysis job."""
from __future__ import annotations

import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import engine
from app.stands import terrain as terrain_mod
from app.stands.models import Stand


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
