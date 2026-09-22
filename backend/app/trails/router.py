"""Route handlers for /api/trails."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from app.dependencies import require_token
from app.trails import service

router = APIRouter(tags=["trails"])


@router.get("/api/trails")
async def trails(south: float, west: float, north: float, east: float, zoom: int, _=Depends(require_token)):
    """Off-road trails/roads (USFS MVUM) inside a map bounding box, for zoom >= 11."""
    try:
        return await service.trails(south, west, north, east, zoom)
    except service.BadBounds as e:
        raise HTTPException(400, str(e))
    except service.TrailsUnavailable as e:
        raise HTTPException(502, str(e))
