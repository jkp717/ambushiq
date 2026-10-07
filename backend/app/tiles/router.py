"""Route handlers for /api/tiles (terrain overlay tiles)."""
from __future__ import annotations

from functools import lru_cache

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

from app.tiles import service

router = APIRouter(tags=["tiles"])


@lru_cache(maxsize=1)
def _empty() -> bytes:
    return service.empty_tile()


# Deliberately no require_token: these are public USGS/MRLC images from fixed upstream services, map
# tiles are loaded by <img> tags that can't send an Authorization header, and a token in the URL would
# change the tile URLs that saved offline areas are keyed by whenever the token is rotated.
@router.get("/api/tiles/{layer}/{z}/{x}/{y}.png")
async def overlay_tile(layer: str, z: int, x: int, y: int):
    if not service.tile_in_range(layer, z, x, y):
        raise HTTPException(404, "no such tile")
    try:
        data = await service.get_tile(layer, z, x, y)
    except service.UpstreamError:
        # Blank and uncached, so the next view tries upstream again instead of keeping a hole.
        return Response(_empty(), media_type="image/png", headers={"Cache-Control": "no-store"})
    return Response(data, media_type="image/png", headers={"Cache-Control": "public, max-age=2592000"})
