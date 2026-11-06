"""Route handlers for /api/search (map search box)."""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, HTTPException

from app.dependencies import require_token
from app.search import service

router = APIRouter(tags=["search"])


@router.get("/api/search")
async def search(q: str, lat: float, lon: float, _=Depends(require_token)):
    """Towns, streets, waterways, landforms and trails matching `q`, nearest the map view (lat, lon) first.
    `failed` lists sources that couldn't answer in time (the others still return)."""
    try:
        return await service.search(q, lat, lon)
    except service.BadQuery as e:
        raise HTTPException(400, str(e))


@router.get("/api/search/osm-trail/{row_id}")
async def osm_trail(row_id: int, _=Depends(require_token)):
    """The full line of an OpenStreetMap trail picked from the search results."""
    f = await asyncio.to_thread(service.osm_trail_line, row_id)
    if f is None:
        raise HTTPException(404, "trail not found (the trail data may have been refreshed; search again)")
    return f
