"""Route handlers for /api/roads."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from app.dependencies import require_token
from app.roads import service

router = APIRouter(tags=["roads"])


@router.get("/api/roads")
async def roads(south: float, west: float, north: float, east: float, zoom: int, _=Depends(require_token)):
    """General road network (USGS National Map) inside a map bounding box, for zoom >= 11."""
    try:
        return await service.roads(south, west, north, east, zoom)
    except service.BadBounds as e:
        raise HTTPException(400, str(e))
    except service.RoadsUnavailable as e:
        raise HTTPException(502, str(e))
