"""Route handlers for /api/tiles (terrain overlay tiles) and /api/tiles/prefetch (overnight preparation)."""
from __future__ import annotations

import asyncio
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field

from app.dependencies import require_token
from app.tiles import prefetch, service

router = APIRouter(tags=["tiles"])

# A failed tile is a 503, not a blank image: a blank looks like "no contours here" to the map, which then never
# asks again. The 503 lets the map retry it (HuntMap's tileerror handler) and is never cached anywhere.
_FAILED_HEADERS = {"Cache-Control": "no-store", "Retry-After": "5"}


class PrefetchArea(BaseModel):
    north: float
    south: float
    west: float
    east: float
    layers: list[str]
    min_zoom: int = Field(ge=0, le=22)
    max_zoom: int = Field(ge=0, le=22)

    def bounds(self) -> prefetch.Bounds:
        b = (self.north, self.south, self.west, self.east)
        if not prefetch.valid_bounds(b) or self.min_zoom > self.max_zoom:
            raise HTTPException(422, "invalid area")
        return b


class PrefetchRequest(PrefetchArea):
    region_id: Optional[int] = None
    label: str = ""


@router.post("/api/tiles/prefetch/check")
async def prefetch_check(body: PrefetchArea, _=Depends(require_token)):
    """How much of this area's contour/hillshade tiles the server already has."""
    try:
        return await asyncio.to_thread(prefetch.check, body.bounds(), body.layers, body.min_zoom, body.max_zoom)
    except ValueError as e:
        raise HTTPException(422, str(e))


@router.post("/api/tiles/prefetch")
async def prefetch_queue(body: PrefetchRequest, _=Depends(require_token)):
    """Queue an area's contour/hillshade tiles for the overnight prefetch."""
    try:
        return await asyncio.to_thread(prefetch.queue_area, body.bounds(), body.layers, body.min_zoom,
                                       body.max_zoom, body.region_id, body.label)
    except ValueError as e:
        raise HTTPException(422, str(e))


@router.get("/api/tiles/prefetch")
def prefetch_list(region_id: Optional[int] = None, _=Depends(require_token)):
    return prefetch.list_areas(region_id)


# Deliberately no require_token: these are public USGS/MRLC images from fixed upstream services, map
# tiles are loaded by <img> tags that can't send an Authorization header, and a token in the URL would
# change the tile URLs that saved offline areas are keyed by whenever the token is rotated.
@router.get("/api/tiles/{layer}/{z}/{x}/{y}.png")
async def overlay_tile(layer: str, z: int, x: int, y: int, request: Request):
    if not service.tile_in_range(layer, z, x, y):
        raise HTTPException(404, "no such tile")

    async def still_wanted() -> bool:
        return not await request.is_disconnected()

    try:
        data = await service.get_tile(layer, z, x, y, still_wanted)
    except (service.TileAbandoned, service.UpstreamError):
        # Abandoned: the browser already dropped this tile, so nobody reads this answer.
        # Upstream failures are logged by the service, once per block rather than once per waiting request.
        return Response(status_code=503, headers=_FAILED_HEADERS)
    return Response(data, media_type="image/png", headers={"Cache-Control": "public, max-age=2592000"})
