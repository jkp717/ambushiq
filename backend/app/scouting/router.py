"""Route handlers for /api/scouting."""
from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from app.core.database import engine
from app.dependencies import get_active_region_id, require_token
from app.forecast.service import _haversine_m
from app.scouting import service
from app.scouting.models import ScoutingSuggestion
from app.scouting.schemas import ScoutingAnalyzeIn, ScoutingBulkIn, ScoutingStatusIn
from app.settings.service import get_settings

router = APIRouter(prefix="/api/scouting", tags=["scouting"])


@router.get("")
def list_suggestions(region_id: int = Depends(get_active_region_id), _=Depends(require_token)):
    with Session(engine) as s:
        return [r.to_dict() for r in s.scalars(
            select(ScoutingSuggestion).where(ScoutingSuggestion.region_id == region_id)).all()]


@router.get("/overlap")
def check_overlap(lat: float, lon: float, radius_m: float,
                   region_id: int = Depends(get_active_region_id), _=Depends(require_token)):
    with Session(engine) as s:
        rows = s.scalars(
            select(ScoutingSuggestion).where(ScoutingSuggestion.region_id == region_id)).all()
    hits = [r.to_dict() for r in rows if _haversine_m(lat, lon, r.lat, r.lon) <= radius_m + r.radius_m]
    return {"overlaps": bool(hits), "suggestions": hits}


@router.post("/analyze")
async def analyze(body: ScoutingAnalyzeIn, region_id: int = Depends(get_active_region_id),
                   _=Depends(require_token)):
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
                    body.lat, body.lon, body.radius_m, mode, settings, region_id, progress_callback=cb)
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


@router.post("/bulk")
def bulk_update(body: ScoutingBulkIn, region_id: int = Depends(get_active_region_id),
                _=Depends(require_token)):
    """Delete, dismiss or restore many suggestions at once. Only rows in the active region
    are touched; ids from other regions (or that don't exist) are silently ignored."""
    if not body.ids:
        return {"ok": True, "affected": 0}
    with Session(engine) as s:
        match = (ScoutingSuggestion.region_id == region_id, ScoutingSuggestion.id.in_(body.ids))
        if body.action == "delete":
            result = s.execute(delete(ScoutingSuggestion).where(*match))
        else:
            status = "dismissed" if body.action == "dismiss" else "new"
            result = s.execute(update(ScoutingSuggestion).where(*match).values(status=status))
        s.commit()
        return {"ok": True, "affected": result.rowcount}


@router.put("/{suggestion_id}")
def update_status(suggestion_id: int, body: ScoutingStatusIn, region_id: int = Depends(get_active_region_id),
                   _=Depends(require_token)):
    with Session(engine) as s:
        row = s.get(ScoutingSuggestion, suggestion_id)
        if not row or row.region_id != region_id:
            raise HTTPException(404, "not found")
        row.status = body.status
        s.commit()
        s.refresh(row)
        return row.to_dict()


@router.delete("/{suggestion_id}")
def delete_suggestion(suggestion_id: int, region_id: int = Depends(get_active_region_id),
                       _=Depends(require_token)):
    with Session(engine) as s:
        row = s.get(ScoutingSuggestion, suggestion_id)
        if row and row.region_id == region_id:
            s.delete(row)
            s.commit()
    return {"ok": True}
