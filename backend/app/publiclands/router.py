"""Route handlers for /api/public-lands."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from app.dependencies import require_token
from app.publiclands import service

router = APIRouter(tags=["publiclands"])


@router.get("/api/public-lands")
async def public_lands(south: float, west: float, north: float, east: float, zoom: int, _=Depends(require_token)):
    """Public land boundaries (USGS PAD-US) inside a map bounding box, for zoom >= 11."""
    try:
        return await service.public_lands(south, west, north, east, zoom)
    except service.BadBounds as e:
        raise HTTPException(400, str(e))
    except service.PublicLandsUnavailable as e:
        raise HTTPException(502, str(e))
