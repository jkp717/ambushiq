"""Terrain overlay tiles — hillshade, contour lines and a woods/water tint — for stacking on the base maps.

None of these exist as ready-made map tiles at the detail we want: USGS 3DEP renders hillshade and contours
on request for any bounding box (sharp to zoom ~18 where lidar exists, and contours come as plain dark lines),
and NLCD land cover is a WMS. So tiles are fetched once from upstream, recolored here into a transparent
overlay, and kept on disk; after that each is a plain file read. Because the result is an ordinary
{z}/{x}/{y} URL, Leaflet and the offline download treat it like any other tile layer.

3DEP takes about as long for a 1024 px image as for a 256 px one (~7-15 s cold, and it answers 504 when
busy), so tiles are fetched as aligned META x META blocks: one upstream request is cut into 16 tiles, all
cached, so neighbours arrive with the first one and far fewer slow requests hit USGS.

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
from typing import Awaitable, Callable, Optional

import httpx
from PIL import Image, ImageFilter

log = logging.getLogger(__name__)

TILE_CACHE_DIR = os.environ.get("TILE_CACHE_DIR", "/app/data/tiles")
TILE_PX = 256
META = 4                       # tiles per side of one upstream request (a META*TILE_PX image)
REQUEST_TIMEOUT_S = 45.0       # 3DEP takes 5-30 s for a dense 10 ft contour tile
RETRY_DELAY_S = 1.5            # one more try after a gateway error/timeout (USGS 502/504s are intermittent)
RETRY_STATUS = {500, 502, 503, 504}
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
# Tiles being fetched right now -> (task, the waiting requests' still_wanted checks). A browser retry, or two
# layers wanting the same tile, joins the running fetch instead of queuing a second upstream call.
_inflight: dict[tuple, tuple[asyncio.Task, list]] = {}


class UpstreamError(Exception):
    """The upstream image service failed or returned something that isn't an image."""


class TileAbandoned(Exception):
    """The browser stopped waiting for this tile (panned/zoomed away) before its upstream fetch started."""


# Async "does anyone still want this tile?" check, e.g. the request's not-disconnected test.
StillWanted = Callable[[], Awaitable[bool]]


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


def block_of(x: int, y: int) -> tuple[int, int]:
    """Top-left tile (x0, y0) of the META x META block holding tile x/y."""
    return x - x % META, y - y % META


def block_bbox_3857(z: int, x0: int, y0: int) -> tuple[float, float, float, float]:
    """(minx, miny, maxx, maxy) of the META x META block of tiles whose top-left tile is x0/y0."""
    minx, _, _, maxy = tile_bbox_3857(z, x0, y0)
    _, miny, maxx, _ = tile_bbox_3857(z, x0 + META - 1, y0 + META - 1)
    return minx, miny, maxx, maxy


def _upstream_request(layer: str, z: int, x0: int, y0: int) -> tuple[str, dict]:
    """The upstream request for the block whose top-left tile is x0/y0."""
    bbox = ",".join(f"{v:.3f}" for v in block_bbox_3857(z, x0, y0))
    px = META * TILE_PX
    if layer == "landcover":
        return _NLCD, {
            "service": "WMS", "version": "1.1.1", "request": "GetMap", "layers": "NLCD_2021_Land_Cover_L48",
            "styles": "", "srs": "EPSG:3857", "bbox": bbox, "width": px, "height": px,
            "format": "image/png", "transparent": "true",
        }
    if layer == "hillshade":
        fn = "Hillshade Multidirectional"
    else:  # contours: 10 ft lines turn solid when zoomed out, so use the coarser preset there
        fn = "Preset 10ft Contour Interval" if z >= 15 else "Contour 25"
    return _3DEP, {
        "bbox": bbox, "bboxSR": 3857, "imageSR": 3857, "size": f"{px},{px}", "format": "png",
        "renderingRule": json.dumps({"rasterFunction": fn}), "f": "image",
    }


async def _fetch(layer: str, z: int, x0: int, y0: int, still_wanted: Optional[StillWanted] = None) -> bytes:
    """The upstream image for the block whose top-left tile is x0/y0."""
    global _sem, _client
    if _sem is None:
        _sem = asyncio.Semaphore(6)       # don't flood USGS when a whole screen of tiles misses at once
    if _client is None:
        _client = httpx.AsyncClient(timeout=REQUEST_TIMEOUT_S)
    url, params = _upstream_request(layer, z, x0, y0)
    for attempt in (1, 2):
        if attempt == 2:
            await asyncio.sleep(RETRY_DELAY_S)   # outside the semaphore, so other tiles keep moving
        async with _sem:
            # Quick panning queues many tiles behind the semaphore; ones the map already dropped are skipped
            # here so the tiles on screen now don't wait behind them. A fetch that has started runs to the
            # end and gets cached, since that work is already paid for.
            if still_wanted is not None and not await still_wanted():
                raise TileAbandoned()
            try:
                r = await _client.get(url, params=params)
            except httpx.HTTPError as e:     # timeout / connection trouble: worth one more try
                err = UpstreamError(f"{type(e).__name__}: {e}")
                err.__cause__ = e
                continue
        if r.status_code in RETRY_STATUS:
            err = UpstreamError(f"HTTP {r.status_code}")
            continue
        if r.status_code != 200 or not r.headers.get("content-type", "").startswith("image/"):
            raise UpstreamError(f"HTTP {r.status_code} {r.headers.get('content-type', '')}")
        return r.content
    raise err


def _png(im: Image.Image) -> bytes:
    buf = io.BytesIO()
    im.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def _rgba(src: Image.Image | bytes) -> Image.Image:
    return (src if isinstance(src, Image.Image) else Image.open(io.BytesIO(src))).convert("RGBA")


def _luma_alpha(raw: Image.Image | bytes) -> tuple[Image.Image, Image.Image]:
    """Grayscale + alpha of an upstream image (alpha from its own transparency, if it has any)."""
    rgba = _rgba(raw)
    return rgba.convert("L"), rgba.getchannel("A")


def recolor_contours(raw: Image.Image | bytes) -> bytes:
    """Dark lines on a light (or transparent) background -> CONTOUR_RGB lines on a transparent background."""
    gray, src_alpha = _luma_alpha(raw)
    darkness = gray.point(lambda v: max(0, min(255, (230 - v) * 255 // 150)))
    alpha = Image.composite(darkness, Image.new("L", gray.size, 0), src_alpha.point(lambda a: 255 if a >= 128 else 0))
    out = Image.new("RGBA", gray.size, CONTOUR_RGB + (0,))
    out.putalpha(alpha)
    return _png(out)


def recolor_hillshade(raw: Image.Image | bytes) -> bytes:
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


def recolor_landcover(raw: Image.Image | bytes) -> bytes:
    """NLCD classes -> woods/water tints (everything else transparent), softened so 30 m cells don't look blocky."""
    im = _rgba(raw)
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


async def get_tile(layer: str, z: int, x: int, y: int, still_wanted: Optional[StillWanted] = None) -> bytes:
    """The overlay tile, from the disk cache or freshly fetched + recolored. Raises UpstreamError, or
    TileAbandoned when `still_wanted` says the caller gave up while the fetch was waiting its turn."""
    path = _cache_path(layer, z, x, y)
    cached = await asyncio.to_thread(_read_cached, path)
    if cached is not None:
        return cached
    x0, y0 = block_of(x, y)
    key = (layer, z, x0, y0)          # requests for any tile of a block share its one upstream fetch
    entry = _inflight.get(key)
    if entry is None:
        wanters: list = []

        async def anyone_wants() -> bool:
            for check in list(wanters):
                if check is None or await check():
                    return True
            return False

        task = asyncio.create_task(_fetch_block(layer, z, x0, y0, anyone_wants))
        entry = _inflight[key] = (task, wanters)
        task.add_done_callback(lambda t: _inflight.pop(key) if _inflight.get(key, (None,))[0] is t else None)
    task, wanters = entry
    wanters.append(still_wanted)
    try:
        # shielded: one request giving up mustn't cancel the fetch others are waiting on
        return (await asyncio.shield(task))[(x, y)]
    finally:
        wanters.remove(still_wanted)


async def _fetch_block(layer: str, z: int, x0: int, y0: int, still_wanted: StillWanted) -> dict:
    """Fetch one block upstream, cut it into tiles, recolor and cache each: {(x, y): png bytes}.
    A failure is logged here, once per block, however many requests were waiting on it."""
    try:
        raw = await _fetch(layer, z, x0, y0, still_wanted)
        try:
            return await asyncio.to_thread(_split_and_store, layer, z, x0, y0, raw)
        except Exception as e:  # Pillow couldn't read what came back
            raise UpstreamError(f"bad image: {e}") from e
    except UpstreamError as e:
        log.warning("tiles %s/%s/%s-%s/%s-%s failed: %s", layer, z, x0, x0 + META - 1, y0, y0 + META - 1, e)
        raise


def _split_and_store(layer: str, z: int, x0: int, y0: int, raw: bytes) -> dict:
    im = Image.open(io.BytesIO(raw))
    im.load()
    side = META * TILE_PX
    if im.size != (side, side):          # not expected, but cut the right grid regardless
        im = im.resize((side, side))
    out = {}
    for dy in range(META):
        for dx in range(META):
            crop = im.crop((dx * TILE_PX, dy * TILE_PX, (dx + 1) * TILE_PX, (dy + 1) * TILE_PX))
            data = _RECOLOR[layer](crop)
            _write_cached(_cache_path(layer, z, x0 + dx, y0 + dy), data)
            out[(x0 + dx, y0 + dy)] = data
    return out


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
