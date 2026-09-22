"""Route handlers for /api/recreation-sites."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from app.dependencies import require_token
from app.recsites import service

router = APIRouter(tags=["recsites"])


@router.get("/api/recreation-sites")
async def recreation_sites(south: float, west: float, north: float, east: float, zoom: int, _=Depends(require_token)):
    """Recreation sites (USFS: trailheads, campgrounds, picnic sites, day-use areas) inside a map
    bounding box, for zoom >= 11."""
    try:
        return await service.recreation_sites(south, west, north, east, zoom)
    except service.BadBounds as e:
        raise HTTPException(400, str(e))
    except service.RecSitesUnavailable as e:
        raise HTTPException(502, str(e))
