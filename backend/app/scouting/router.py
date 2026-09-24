"""Route handlers for /api/scouting."""
from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import engine
from app.dependencies import get_active_region_id, require_token
from app.forecast.service import _haversine_m
from app.scouting import service
from app.scouting.models import ScoutingSuggestion
from app.scouting.schemas import ScoutingAnalyzeIn, ScoutingColorIn, ScoutingCommentIn, ScoutingStatusIn
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


def _own_suggestion(s: Session, suggestion_id: int, region_id: int) -> ScoutingSuggestion:
    row = s.get(ScoutingSuggestion, suggestion_id)
    if not row or row.region_id != region_id:
        raise HTTPException(404, "not found")
    return row


@router.put("/{suggestion_id}/color")
def set_color(suggestion_id: int, body: ScoutingColorIn, region_id: int = Depends(get_active_region_id),
              _=Depends(require_token)):
    with Session(engine) as s:
        row = _own_suggestion(s, suggestion_id, region_id)
        row.color = body.color.upper() if body.color else None
        s.commit()
        s.refresh(row)
        return row.to_dict()


@router.post("/{suggestion_id}/comments")
def add_comment(suggestion_id: int, body: ScoutingCommentIn, region_id: int = Depends(get_active_region_id),
                _=Depends(require_token)):
    with Session(engine) as s:
        row = _own_suggestion(s, suggestion_id, region_id)
        comments = row.comments()
        comments.append({"id": uuid.uuid4().hex[:12], "text": body.text,
                         "created_at": datetime.now(timezone.utc).isoformat()})
        row.comments_json = json.dumps(comments)
        s.commit()
        s.refresh(row)
        return row.to_dict()


@router.delete("/{suggestion_id}/comments/{comment_id}")
def delete_comment(suggestion_id: int, comment_id: str, region_id: int = Depends(get_active_region_id),
                   _=Depends(require_token)):
    with Session(engine) as s:
        row = _own_suggestion(s, suggestion_id, region_id)
        row.comments_json = json.dumps([c for c in row.comments() if c.get("id") != comment_id])
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
