"""Terrain overlay tiles — hillshade, contour lines and a woods/water tint — for stacking on the base maps.

None of these exist as ready-made map tiles at the detail we want: USGS 3DEP renders hillshade and contours
on request for any bounding box (sharp to zoom ~18 where lidar exists, but ~2 s per image, and contours come
as plain dark lines), and NLCD land cover is a WMS. So each z/x/y tile is fetched once from upstream,
recolored here into a transparent overlay, and kept on disk; after that it is a plain file read. Because the
result is an ordinary {z}/{x}/{y} URL, Leaflet and the offline download treat it like any other tile layer.

A tile's file mtime is its last use (refreshed on a hit at most once a day), so prune_cache can drop tiles
nobody has looked at for months."""
from __future__ import annotations

import asyncio
import io
import json
import logging
import math
import os
import time
from typing import Optional

import httpx
from PIL import Image, ImageFilter

log = logging.getLogger(__name__)

TILE_CACHE_DIR = os.environ.get("TILE_CACHE_DIR", "/app/data/tiles")
TILE_PX = 256
REQUEST_TIMEOUT_S = 30.0
PRUNE_AFTER_S = 180 * 86400
TOUCH_AFTER_S = 86400

_3DEP = "https://elevation.nationalmap.gov/arcgis/rest/services/3DEPElevation/ImageServer/exportImage"
_NLCD = "https://www.mrlc.gov/geoserver/mrlc_display/NLCD_2021_Land_Cover_L48/wms"

# zoom range each layer is rendered at; past the max the map stretches the deepest tiles
LAYERS = {
    "hillshade": (8, 18),
    "contours": (13, 18),
    "landcover": (8, 15),
}

CONTOUR_RGB = (194, 112, 61)   # warm brown-orange: reads on both aerial imagery and a white topo

# NLCD default-style colors -> soft topo-style tints (RGB). Classes not listed (developed, barren,
# grass, pasture, crops) become transparent so only woods and water are colored.
NLCD_TINTS = {
    (104, 170, 99): (197, 226, 180),   # 41 deciduous forest
    (28, 99, 48): (184, 217, 168),     # 42 evergreen forest
    (181, 201, 142): (197, 226, 180),  # 43 mixed forest
    (204, 186, 124): (222, 235, 203),  # 52 shrub/scrub
    (71, 107, 160): (168, 204, 232),   # 11 open water
    (186, 216, 234): (190, 222, 212),  # 90 woody wetlands
    (112, 163, 186): (178, 212, 216),  # 95 emergent herbaceous wetlands
}
_MATCH_DIST2 = 3 * 30 ** 2   # only accept a near-exact palette match

_sem: Optional[asyncio.Semaphore] = None
_client: Optional[httpx.AsyncClient] = None


class UpstreamError(Exception):
    """The upstream image service failed or returned something that isn't an image."""


def tile_in_range(layer: str, z: int, x: int, y: int) -> bool:
    if layer not in LAYERS:
        return False
    lo, hi = LAYERS[layer]
    return lo <= z <= hi and 0 <= x < 2 ** z and 0 <= y < 2 ** z


def tile_bbox_3857(z: int, x: int, y: int) -> tuple[float, float, float, float]:
    """(minx, miny, maxx, maxy) of a slippy-map tile in Web Mercator metres."""
    half = 6378137.0 * math.pi
    size = 2 * half / 2 ** z
    return (-half + x * size, half - (y + 1) * size, -half + (x + 1) * size, half - y * size)


def _upstream_request(layer: str, z: int, x: int, y: int) -> tuple[str, dict]:
    bbox = ",".join(f"{v:.3f}" for v in tile_bbox_3857(z, x, y))
    if layer == "landcover":
        return _NLCD, {
            "service": "WMS", "version": "1.1.1", "request": "GetMap", "layers": "NLCD_2021_Land_Cover_L48",
            "styles": "", "srs": "EPSG:3857", "bbox": bbox, "width": TILE_PX, "height": TILE_PX,
            "format": "image/png", "transparent": "true",
        }
    if layer == "hillshade":
        fn = "Hillshade Multidirectional"
    else:  # contours: 10 ft lines turn solid when zoomed out, so use the coarser preset there
        fn = "Preset 10ft Contour Interval" if z >= 15 else "Contour 25"
    return _3DEP, {
        "bbox": bbox, "bboxSR": 3857, "imageSR": 3857, "size": f"{TILE_PX},{TILE_PX}", "format": "png",
        "renderingRule": json.dumps({"rasterFunction": fn}), "f": "image",
    }


async def _fetch(layer: str, z: int, x: int, y: int) -> bytes:
    global _sem, _client
    if _sem is None:
        _sem = asyncio.Semaphore(6)       # don't flood USGS when a whole screen of tiles misses at once
    if _client is None:
        _client = httpx.AsyncClient(timeout=REQUEST_TIMEOUT_S)
    url, params = _upstream_request(layer, z, x, y)
    async with _sem:
        try:
            r = await _client.get(url, params=params)
        except httpx.HTTPError as e:
            raise UpstreamError(f"{type(e).__name__}: {e}") from e
    if r.status_code != 200 or not r.headers.get("content-type", "").startswith("image/"):
        raise UpstreamError(f"HTTP {r.status_code} {r.headers.get('content-type', '')}")
    return r.content


def _png(im: Image.Image) -> bytes:
    buf = io.BytesIO()
    im.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def _luma_alpha(raw: bytes) -> tuple[Image.Image, Image.Image]:
    """Grayscale + alpha of an upstream image (alpha from its own transparency, if it has any)."""
    rgba = Image.open(io.BytesIO(raw)).convert("RGBA")
    return rgba.convert("L"), rgba.getchannel("A")


def recolor_contours(raw: bytes) -> bytes:
    """Dark lines on a light (or transparent) background -> CONTOUR_RGB lines on a transparent background."""
    gray, src_alpha = _luma_alpha(raw)
    darkness = gray.point(lambda v: max(0, min(255, (230 - v) * 255 // 150)))
    alpha = Image.composite(darkness, Image.new("L", gray.size, 0), src_alpha.point(lambda a: 255 if a >= 128 else 0))
    out = Image.new("RGBA", gray.size, CONTOUR_RGB + (0,))
    out.putalpha(alpha)
    return _png(out)


def recolor_hillshade(raw: bytes) -> bytes:
    """Grayscale shading as-is (the map multiplies it over the base); transparent where there's no data."""
    gray, alpha = _luma_alpha(raw)
    out = Image.merge("LA", (gray, alpha))
    return _png(out)


def _nearest_tint(rgb: tuple[int, int, int]) -> Optional[tuple[int, int, int]]:
    best, dist = None, _MATCH_DIST2
    for src, tint in NLCD_TINTS.items():
        d = sum((a - b) ** 2 for a, b in zip(rgb, src))
        if d < dist:
            best, dist = tint, d
    return best


def recolor_landcover(raw: bytes) -> bytes:
    """NLCD classes -> woods/water tints (everything else transparent), softened so 30 m cells don't look blocky."""
    im = Image.open(io.BytesIO(raw)).convert("RGBA")
    clear = (0, 0, 0, 0)
    lookup: dict[tuple, tuple] = {}

    def tint(px: tuple) -> tuple:
        if px[3] < 128:
            return clear
        key = px[:3]
        if key not in lookup:
            t = _nearest_tint(key)
            lookup[key] = (t + (255,)) if t else clear
        return lookup[key]

    out = Image.new("RGBA", im.size)
    out.putdata([tint(px) for px in im.getdata()])
    return _png(out.filter(ImageFilter.GaussianBlur(1.2)))


_RECOLOR = {"contours": recolor_contours, "hillshade": recolor_hillshade, "landcover": recolor_landcover}


def _cache_path(layer: str, z: int, x: int, y: int) -> str:
    return os.path.join(TILE_CACHE_DIR, layer, str(z), str(x), f"{y}.png")


def _read_cached(path: str) -> Optional[bytes]:
    try:
        with open(path, "rb") as f:
            data = f.read()
    except OSError:
        return None
    try:
        if time.time() - os.path.getmtime(path) > TOUCH_AFTER_S:
            os.utime(path)               # mark as recently used so prune_cache keeps it
    except OSError:
        pass
    return data


def _write_cached(path: str, data: bytes) -> None:
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = f"{path}.{os.getpid()}.tmp"
        with open(tmp, "wb") as f:
            f.write(data)
        os.replace(tmp, path)
    except OSError:
        log.warning("tile cache write failed for %s", path, exc_info=True)


async def get_tile(layer: str, z: int, x: int, y: int) -> bytes:
    """The overlay tile, from the disk cache or freshly fetched + recolored. Raises UpstreamError."""
    path = _cache_path(layer, z, x, y)
    cached = await asyncio.to_thread(_read_cached, path)
    if cached is not None:
        return cached
    raw = await _fetch(layer, z, x, y)
    try:
        data = await asyncio.to_thread(_RECOLOR[layer], raw)
    except Exception as e:  # Pillow couldn't read what came back
        raise UpstreamError(f"bad image: {e}") from e
    await asyncio.to_thread(_write_cached, path, data)
    return data


def empty_tile() -> bytes:
    return _png(Image.new("RGBA", (TILE_PX, TILE_PX), (0, 0, 0, 0)))


def prune_cache(max_age_s: float = PRUNE_AFTER_S) -> int:
    """Delete cached tiles nobody has used for a long time; returns how many files went."""
    cutoff = time.time() - max_age_s
    removed = 0
    for root, _dirs, files in os.walk(TILE_CACHE_DIR):
        for name in files:
            p = os.path.join(root, name)
            try:
                if os.path.getmtime(p) < cutoff:
                    os.remove(p)
                    removed += 1
            except OSError:
                pass
    return removed
