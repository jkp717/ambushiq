"""Route handlers for /api/roads."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from app.dependencies import require_token
from app.roads import service, street_name

router = APIRouter(tags=["roads"])


@router.get("/api/roads/name")
async def road_name(lat: float, lon: float, _=Depends(require_token)):
    """Street name for a point on a road USGS doesn't name (Esri reverse geocode); null when unknown or unreachable."""
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise HTTPException(400, "invalid point")
    return {"name": await street_name.lookup(lat, lon)}


@router.get("/api/roads")
async def roads(south: float, west: float, north: float, east: float, zoom: int, under_fs: bool = False,
                _=Depends(require_token)):
    """General road network (USGS National Map) inside a map bounding box, for zoom >= 11. `under_fs`: the map is
    showing Forest Service roads, so leave out the USGS stretches they already draw."""
    try:
        return await service.roads(south, west, north, east, zoom, under_fs)
    except service.BadBounds as e:
        raise HTTPException(400, str(e))
    except service.RoadsUnavailable as e:
        raise HTTPException(502, str(e))
