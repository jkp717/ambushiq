"""Route handlers for /api/tiles (terrain overlay tiles)."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response

from app.tiles import service

router = APIRouter(tags=["tiles"])

# A failed tile is a 503, not a blank image: a blank looks like "no contours here" to the map, which then never
# asks again. The 503 lets the map retry it (HuntMap's tileerror handler) and is never cached anywhere.
_FAILED_HEADERS = {"Cache-Control": "no-store", "Retry-After": "5"}


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
