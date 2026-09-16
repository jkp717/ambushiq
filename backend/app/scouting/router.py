"""Route handlers for /api/scouting."""
from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import engine
from app.dependencies import require_token
from app.forecast.service import _haversine_m
from app.scouting import service
from app.scouting.models import ScoutingSuggestion
from app.scouting.schemas import ScoutingAnalyzeIn, ScoutingStatusIn
from app.settings.service import get_settings

router = APIRouter(prefix="/api/scouting", tags=["scouting"])


@router.get("")
def list_suggestions(_=Depends(require_token)):
    with Session(engine) as s:
        return [r.to_dict() for r in s.scalars(select(ScoutingSuggestion)).all()]


@router.get("/overlap")
def check_overlap(lat: float, lon: float, radius_m: float, _=Depends(require_token)):
    with Session(engine) as s:
        rows = s.scalars(select(ScoutingSuggestion)).all()
    hits = [r.to_dict() for r in rows if _haversine_m(lat, lon, r.lat, r.lon) <= radius_m + r.radius_m]
    return {"overlaps": bool(hits), "suggestions": hits}


@router.post("/analyze")
async def analyze(body: ScoutingAnalyzeIn, _=Depends(require_token)):
    settings = get_settings()
    radius_min = float(settings.get("scout_radius_min_m", 60.0))
    radius_max = float(settings.get("scout_radius_max_m", 2400.0))
    if not (radius_min <= body.radius_m <= radius_max):
        raise HTTPException(400, f"radius_m must be between {radius_min} and {radius_max}")
    mode = body.mode or "merge"

    async def event_generator():
        queue: asyncio.Queue = asyncio.Queue()

        async def cb(pct, msg):
            await queue.put(json.dumps({"progress": pct, "message": msg}) + "\n")

        async def run_analysis():
            try:
                suggestions = await service.analyze_area(
                    body.lat, body.lon, body.radius_m, mode, settings, progress_callback=cb)
                await queue.put(json.dumps({"progress": 100, "complete": True, "suggestions": suggestions}) + "\n")
            except Exception as e:
                await queue.put(json.dumps({"error": str(e)}) + "\n")
            finally:
                await queue.put(None)

        task = asyncio.create_task(run_analysis())
        while True:
            item = await queue.get()
            if item is None:
                break
            yield item
        await task

    return StreamingResponse(event_generator(), media_type="application/x-ndjson")


@router.put("/{suggestion_id}")
def update_status(suggestion_id: int, body: ScoutingStatusIn, _=Depends(require_token)):
    with Session(engine) as s:
        row = s.get(ScoutingSuggestion, suggestion_id)
        if not row:
            raise HTTPException(404, "not found")
        row.status = body.status
        s.commit()
        s.refresh(row)
        return row.to_dict()


@router.delete("/{suggestion_id}")
def delete_suggestion(suggestion_id: int, _=Depends(require_token)):
    with Session(engine) as s:
        row = s.get(ScoutingSuggestion, suggestion_id)
        if row:
            s.delete(row)
            s.commit()
    return {"ok": True}
