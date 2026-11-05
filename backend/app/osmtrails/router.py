"""Route handlers for /api/osm-trails."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from app.dependencies import require_token
from app.osmtrails import service

router = APIRouter(tags=["osm-trails"])


@router.get("/api/osm-trails")
async def osm_trails(south: float, west: float, north: float, east: float, zoom: int, _=Depends(require_token)):
    """OpenStreetMap foot trails, horse trails and tracks inside a map bounding box, for zoom >= 11, from the
    Geofabrik state extracts imported for the regions' states. `status`: ok | importing | error | none."""
    try:
        return await service.osm_trails(south, west, north, east, zoom)
    except service.BadBounds as e:
        raise HTTPException(400, str(e))
