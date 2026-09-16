"""Route handlers for /api/stands."""
from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import engine
from app.dependencies import get_active_region_id, require_token
from app.stands import terrain as terrain_mod
from app.stands.models import Stand
from app.stands.schemas import StandIn

router = APIRouter(prefix="/api/stands", tags=["stands"])


@router.get("")
def list_stands(region_id: int = Depends(get_active_region_id), _=Depends(require_token)):
    with Session(engine) as s:
        return [r.to_dict() for r in s.scalars(
            select(Stand).where(Stand.region_id == region_id).order_by(Stand.name)).all()]


@router.post("")
def create_stand(body: StandIn, region_id: int = Depends(get_active_region_id), _=Depends(require_token)):
    with Session(engine) as s:
        st = Stand(**body.model_dump(), region_id=region_id)
        s.add(st)
        s.commit()
        s.refresh(st)
        return st.to_dict()


@router.put("/{stand_id}")
def update_stand(stand_id: int, body: StandIn, region_id: int = Depends(get_active_region_id),
                  _=Depends(require_token)):
    with Session(engine) as s:
        st = s.get(Stand, stand_id)
        if not st or st.region_id != region_id:
            raise HTTPException(404, "not found")
        moved = (st.lat != body.lat) or (st.lon != body.lon)
        for k, v in body.model_dump().items():
            setattr(st, k, 1 if (k == "is_active" and v) else (0 if k == "is_active" else v))
        if moved:
            st.terrain_json = None  # invalidate cached terrain on move
        s.commit()
        s.refresh(st)
        return st.to_dict()


@router.delete("/{stand_id}")
def delete_stand(stand_id: int, region_id: int = Depends(get_active_region_id), _=Depends(require_token)):
    with Session(engine) as s:
        st = s.get(Stand, stand_id)
        if st and st.region_id == region_id:
            s.delete(st)
            s.commit()
    return {"ok": True}


# ---------- terrain (cached) ----------
@router.post("/{stand_id}/terrain")
async def analyze_stand_terrain(stand_id: int, region_id: int = Depends(get_active_region_id),
                                 _=Depends(require_token)):
    with Session(engine) as s:
        st = s.get(Stand, stand_id)
        if not st or st.region_id != region_id:
            raise HTTPException(404, "not found")
        lat, lon = st.lat, st.lon

    async def event_generator():
        try:
            # Collect progress events via an async queue
            queue = asyncio.Queue()

            async def cb(pct, msg):
                await queue.put(json.dumps({"progress": pct, "message": msg}) + "\n")

            # Run fetch_terrain in background while draining the queue
            async def run_analysis():
                try:
                    terrain = await terrain_mod.fetch_terrain(lat, lon, progress_callback=cb)
                    with Session(engine) as s:
                        st = s.get(Stand, stand_id)
                        st.terrain_json = json.dumps(terrain)
                        st.downhill_deg = terrain["downhill_deg"]
                        s.commit()
                        s.refresh(st)
                        await queue.put(json.dumps({"progress": 100, "complete": True, "terrain": st.to_dict()}) + "\n")
                except Exception as e:
                    await queue.put(json.dumps({"error": str(e)}) + "\n")
                finally:
                    await queue.put(None) # Sentinel to stop

            task = asyncio.create_task(run_analysis())

            while True:
                item = await queue.get()
                if item is None:
                    break
                yield item
            await task
        except Exception as e:
            yield json.dumps({"error": str(e)}) + "\n"

    return StreamingResponse(event_generator(), media_type="application/x-ndjson")
