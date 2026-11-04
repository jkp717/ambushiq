"""Route handlers for /api/elevation."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from app.dependencies import get_active_region_id, require_token
from app.elevation import service

router = APIRouter(tags=["elevation"])


@router.get("/api/elevation/profile")
async def elevation_profile(a_lat: float, a_lon: float, b_lat: float, b_lon: float,
                            region_id: int = Depends(get_active_region_id), _=Depends(require_token)):
    """Net elevation change and total climb / drop (feet) along the straight line a -> b (the map ruler)."""
    if not (-90 <= a_lat <= 90 and -90 <= b_lat <= 90 and -180 <= a_lon <= 180 and -180 <= b_lon <= 180):
        raise HTTPException(400, "coordinates out of range")
    try:
        return await service.profile((a_lat, a_lon), (b_lat, b_lon), region_id)
    except service.ElevationUnavailable as e:
        raise HTTPException(503, f"elevation unavailable: {e}")
