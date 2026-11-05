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
import numpy as np
from PIL import Image, ImageFilter

log = logging.getLogger(__name__)

TILE_CACHE_DIR = os.environ.get("TILE_CACHE_DIR", "/app/data/tiles")
TILE_PX = 256
META = 4                       # tiles per side of one upstream request (a META*TILE_PX image)
REQUEST_TIMEOUT_S = 45.0       # 3DEP takes 5-30 s for a dense 10 ft contour tile
RETRY_DELAY_S = 1.5            # base wait before another try after a gateway error/timeout
# USGS 502/504s come in bursts (a busy 3DEP answers several zoom-18 contour blocks in a row with a fast 502), so a
# gateway error gets two more tries, backing off 1x then 3x RETRY_DELAY_S. A timeout gets only one more: each one
# already cost REQUEST_TIMEOUT_S, and three would outlast the proxy's 120 s.
RETRY_BACKOFF = (1, 3)
RETRY_STATUS = {429, 500, 502, 503, 504}

# How many upstream requests run at once, per host, adapts to how the host is coping (AIMD): a 5xx/429/timeout
# halves it and pauses new requests for a while; a run of successes adds one back. 3DEP answers overload with
# bursts of 502/504 rather than a 429, and hammering it with retries during a burst only makes it worse.
LIMIT_START = 4
LIMIT_MIN = 1
LIMIT_MAX = 6
GROW_AFTER = 8                 # successes in a row before the limit goes up by one
PAUSE_MIN_S = 5.0              # first pause after pushback; doubles on each further pushback, up to PAUSE_MAX_S
PAUSE_MAX_S = 60.0
BACKGROUND_MAX = 2             # prefetch never uses more than this many slots, and leaves one free for the map
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


class _UpstreamLimiter:
    """Adaptive cap on concurrent requests to one upstream host (see LIMIT_START).

    Background requests (the overnight prefetch) take a slot only when no map request is waiting, never more than
    BACKGROUND_MAX of them, and leave one slot free while the limit allows it, so someone viewing the map isn't
    stuck behind the prefetch."""

    def __init__(self, host: str):
        self.host = host
        self.loop = asyncio.get_running_loop()
        self.limit = LIMIT_START
        self.active = 0
        self.background_active = 0
        self.live_waiting = 0
        self.streak = 0
        self.pause_until = 0.0
        self.next_pause = PAUSE_MIN_S
        self._cond = asyncio.Condition()

    def _may_start(self, background: bool) -> bool:
        if self.active >= self.limit:
            return False
        if background:
            if self.live_waiting or self.background_active >= BACKGROUND_MAX:
                return False
            if self.limit > 1 and self.active >= self.limit - 1:
                return False
        return True

    async def acquire(self, background: bool = False) -> None:
        async with self._cond:
            if not background:
                self.live_waiting += 1
            try:
                while True:
                    wait = self.pause_until - time.monotonic()
                    if wait <= 0 and self._may_start(background):
                        break
                    try:
                        await asyncio.wait_for(self._cond.wait(), timeout=wait if wait > 0 else None)
                    except asyncio.TimeoutError:
                        pass
            finally:
                if not background:
                    self.live_waiting -= 1
                    self._cond.notify_all()      # a background request may have been waiting on this one
            self.active += 1
            if background:
                self.background_active += 1

    async def release(self, background: bool, ok: Optional[bool], detail: str = "", secs: float = 0.0,
                      retry_after: Optional[float] = None) -> None:
        """`ok`: True = success, False = the host pushed back (5xx/429/timeout), None = neither (e.g. a 400)."""
        async with self._cond:
            self.active -= 1
            if background:
                self.background_active -= 1
            now = time.monotonic()
            if ok:
                self.streak += 1
                if self.streak >= GROW_AFTER:
                    self.streak = 0
                    self.next_pause = PAUSE_MIN_S
                    if self.limit < LIMIT_MAX:
                        self.limit += 1
                        log.info("%s: upstream limit raised to %d", self.host, self.limit)
            elif ok is False:
                self.streak = 0
                if now >= self.pause_until:          # one burst of failures counts once, not once per request
                    old = self.limit
                    self.limit = max(LIMIT_MIN, self.limit // 2)
                    pause = retry_after if retry_after is not None else self.next_pause
                    self.pause_until = now + pause
                    self.next_pause = min(PAUSE_MAX_S, self.next_pause * 2)
                    log.warning("%s pushed back (%s after %.1f s): upstream limit %d -> %d, pausing %.0f s",
                                self.host, detail, secs, old, self.limit, pause)
            self._cond.notify_all()


_limiters: dict[str, _UpstreamLimiter] = {}


def _limiter_for(url: str) -> _UpstreamLimiter:
    host = httpx.URL(url).host
    lim = _limiters.get(host)
    if lim is None or lim.loop is not asyncio.get_running_loop():   # (a new event loop only happens in tests)
        lim = _limiters[host] = _UpstreamLimiter(host)
    return lim


def _retry_after(r) -> Optional[float]:
    try:
        return max(0.0, min(PAUSE_MAX_S * 5, float(r.headers.get("retry-after", ""))))
    except (TypeError, ValueError):
        return None


async def _fetch(layer: str, z: int, x0: int, y0: int, still_wanted: Optional[StillWanted] = None,
                 background: bool = False) -> bytes:
    """The upstream image for the block whose top-left tile is x0/y0."""
    global _client
    if _client is None:
        _client = httpx.AsyncClient(timeout=REQUEST_TIMEOUT_S)
    url, params = _upstream_request(layer, z, x0, y0)
    lim = _limiter_for(url)
    timed_out = False
    err: Optional[UpstreamError] = None
    for attempt in range(1 + len(RETRY_BACKOFF)):
        if attempt:
            if timed_out and attempt >= 2:
                break                            # a second slow attempt in a row: give up rather than hold the request
            await asyncio.sleep(RETRY_DELAY_S * RETRY_BACKOFF[attempt - 1])   # outside the limiter, so other tiles keep moving
        await lim.acquire(background)
        ok: Optional[bool] = None
        detail, retry_after = "", None
        started = time.monotonic()
        try:
            # Quick panning queues many tiles behind the limiter; ones the map already dropped are skipped
            # here so the tiles on screen now don't wait behind them. A fetch that has started runs to the
            # end and gets cached, since that work is already paid for.
            if still_wanted is not None and not await still_wanted():
                raise TileAbandoned()
            try:
                r = await _client.get(url, params=params)
            except httpx.HTTPError as e:     # timeout / connection trouble: worth another try
                err = UpstreamError(f"{type(e).__name__}: {e}")
                err.__cause__ = e
                timed_out = isinstance(e, httpx.TimeoutException)
                ok, detail = False, type(e).__name__
                continue
            timed_out = False
            log.info("upstream %s/%s/%s/%s: HTTP %s in %.1f s (limit %d)", layer, z, x0, y0, r.status_code,
                     time.monotonic() - started, lim.limit)
            if r.status_code in RETRY_STATUS:
                err = UpstreamError(f"HTTP {r.status_code}")
                ok, detail = False, f"HTTP {r.status_code}"
                retry_after = _retry_after(r) if r.status_code == 429 else None
                continue
            if r.status_code != 200 or not r.headers.get("content-type", "").startswith("image/"):
                raise UpstreamError(f"HTTP {r.status_code} {r.headers.get('content-type', '')}")
            ok = True
            return r.content
        finally:
            await lim.release(background, ok, detail, time.monotonic() - started, retry_after)
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


def block_tiles(x0: int, y0: int) -> list[tuple[int, int]]:
    return [(x0 + dx, y0 + dy) for dy in range(META) for dx in range(META)]


def block_cached(layer: str, z: int, x0: int, y0: int) -> bool:
    """Whether every tile of the block whose top-left tile is x0/y0 is in the disk cache."""
    return all(os.path.isfile(_cache_path(layer, z, x, y)) for x, y in block_tiles(x0, y0))


def touch_block(layer: str, z: int, x0: int, y0: int) -> None:
    """Marks a cached block as recently used (as a view of it would), so prune_cache keeps it."""
    now = time.time()
    for x, y in block_tiles(x0, y0):
        path = _cache_path(layer, z, x, y)
        try:
            if now - os.path.getmtime(path) > TOUCH_AFTER_S:
                os.utime(path)
        except OSError:
            pass


async def get_tile(layer: str, z: int, x: int, y: int, still_wanted: Optional[StillWanted] = None,
                   background: bool = False) -> bytes:
    """The overlay tile, from the disk cache or freshly fetched + recolored. Raises UpstreamError, or
    TileAbandoned when `still_wanted` says the caller gave up while the fetch was waiting its turn.
    `background` (the overnight prefetch) gives way to map requests for upstream slots; a map request for a
    block the prefetch is already fetching just joins that fetch."""
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

        task = asyncio.create_task(_fetch_block(layer, z, x0, y0, anyone_wants, background))
        entry = _inflight[key] = (task, wanters)
        task.add_done_callback(lambda t: _inflight.pop(key) if _inflight.get(key, (None,))[0] is t else None)
    task, wanters = entry
    wanters.append(still_wanted)
    try:
        # shielded: one request giving up mustn't cancel the fetch others are waiting on
        return (await asyncio.shield(task))[(x, y)]
    finally:
        wanters.remove(still_wanted)


async def _fetch_block(layer: str, z: int, x0: int, y0: int, still_wanted: StillWanted,
                       background: bool = False) -> dict:
    """Fetch one block upstream, cut it into tiles, recolor and cache each: {(x, y): png bytes}.
    A failure is logged here, once per block, however many requests were waiting on it.

    3DEP sometimes answers 200 with an image that has a blank rectangle in it (seen as a straight-edged hole in the
    contours at one zoom). Since tiles are cached for good, a block is checked before it's stored:
    - contours with a hole (many lines stopping dead along one straight row/column, see hole_score) are fetched up to
      HOLE_RETRIES more times, keeping the attempt with the lowest score;
    - a block that is partly blank (some tiles empty, others not) is fetched once more and each tile keeps whichever
      version has content. A wholly blank block is accepted: flat land really has no 10 ft contours, and it shouldn't
      pay for a second slow request."""
    try:
        raw = await _fetch(layer, z, x0, y0, still_wanted, background)
        tiles, score = await _split_checked(layer, z, x0, y0, raw)
    except UpstreamError as e:
        log.warning("tiles %s/%s/%s-%s/%s-%s failed: %s", layer, z, x0, x0 + META - 1, y0, y0 + META - 1, e)
        raise
    for _ in range(HOLE_RETRIES):
        if score < HOLE_SCORE:
            break
        log.info("tiles %s/%s/%s/%s: block has a hole (score %d), fetching it again", layer, z, x0, y0, score)
        again = await _refetch(layer, z, x0, y0, background)
        if again is not None and again[1] < score:
            tiles, score = again
    blank = {k for k, data in tiles.items() if _is_blank(layer, data)}
    if blank and len(blank) < len(tiles):
        log.info("tiles %s/%s/%s/%s: %d of %d tiles came back blank, fetching the block again",
                 layer, z, x0, y0, len(blank), len(tiles))
        again = await _refetch(layer, z, x0, y0, background)
        if again is not None:
            for k in blank:
                if not _is_blank(layer, again[0][k]):
                    tiles[k] = again[0][k]
    await asyncio.to_thread(_store, layer, z, tiles)
    return tiles


async def _refetch(layer: str, z: int, x0: int, y0: int, background: bool = False) -> Optional[tuple[dict, int]]:
    """Another try at a block that came back damaged; None if it fails (the caller keeps what it has)."""
    await asyncio.sleep(RETRY_DELAY_S)
    try:
        return await _split_checked(layer, z, x0, y0, await _fetch(layer, z, x0, y0, background=background))
    except UpstreamError as e:
        log.warning("tiles %s/%s/%s/%s: refetch failed: %s", layer, z, x0, y0, e)
        return None


async def _split_checked(layer: str, z: int, x0: int, y0: int, raw: bytes) -> tuple[dict, int]:
    try:
        return await asyncio.to_thread(_split, layer, x0, y0, raw)
    except Exception as e:  # Pillow couldn't read what came back
        raise UpstreamError(f"bad image: {e}") from e


def _split(layer: str, x0: int, y0: int, raw: bytes) -> tuple[dict, int]:
    """Cut a block image into recolored tiles: ({(x, y): png bytes}, hole score (contours only, else 0))."""
    im = Image.open(io.BytesIO(raw))
    im.load()
    side = META * TILE_PX
    if im.size != (side, side):          # not expected, but cut the right grid regardless
        im = im.resize((side, side))
    out = {}
    for dy in range(META):
        for dx in range(META):
            crop = im.crop((dx * TILE_PX, dy * TILE_PX, (dx + 1) * TILE_PX, (dy + 1) * TILE_PX))
            out[(x0 + dx, y0 + dy)] = _RECOLOR[layer](crop)
    score = hole_score(_upstream_ink(im)) if layer == "contours" else 0
    return out, score


def _store(layer: str, z: int, tiles: dict) -> None:
    for (x, y), data in tiles.items():
        _write_cached(_cache_path(layer, z, x, y), data)


# ---------- spotting damaged blocks ----------
# Contour lines never stop in the middle of an image: they close on themselves or run off its edge. Where 3DEP left a
# blank rectangle, every line crossing its edge stops dead along the same straight row or column. On real 1024 px
# blocks the most line ends found on one interior row/column is 3-9 (turning points that happen to line up); a cut-in
# hole gives 17-57.
HOLE_SCORE = 16
HOLE_RETRIES = 2       # extra fetches of a holed block; a real straight edge in the data just keeps the best attempt
_HOLE_GAP = 12         # px of nothing past a line's end
_HOLE_WIN = 2          # rows either side that count as "the line carries on"
_HOLE_MARGIN = 8       # lines ending at the image's own edge are normal


def hole_score(ink: np.ndarray) -> int:
    """The most separate lines that stop dead at one interior column or row (looking both ways) of a boolean
    line mask. High = a straight-edged blank patch in the contours."""
    best = 0
    for m in (ink, ink.T):
        rows, n = m.shape
        if n <= _HOLE_GAP + 2 * _HOLE_MARGIN:
            continue
        near = m.copy()                 # a line "carries on" if there's ink within _HOLE_WIN rows
        for d in range(1, _HOLE_WIN + 1):
            near[d:] |= m[:-d]
            near[:-d] |= m[d:]
        cs = np.concatenate([np.zeros((rows, 1), np.int32), np.cumsum(near, axis=1, dtype=np.int32)], axis=1)
        c = np.arange(_HOLE_MARGIN, n - _HOLE_GAP - _HOLE_MARGIN)
        ends_right = m[:, c] & ((cs[:, c + 1 + _HOLE_GAP] - cs[:, c + 1]) == 0)
        ends_left = m[:, c] & ((cs[:, c] - cs[:, c - _HOLE_GAP]) == 0)
        for ends in (ends_right, ends_left):
            # count each line once: the first row of each run of line-end pixels in a column
            starts = ends & ~np.vstack([np.zeros((1, ends.shape[1]), bool), ends[:-1]])
            best = max(best, int(starts.sum(axis=0).max()))
    return best


def _upstream_ink(im: Image.Image) -> np.ndarray:
    """Line mask of a raw 3DEP contour image: dark, visible pixels."""
    rgba = im.convert("RGBA")
    return (np.asarray(rgba.getchannel("A")) >= 128) & (np.asarray(rgba.convert("L")) < 200)


def _cached_ink(data: bytes) -> np.ndarray:
    """Line mask of a cached (recolored) contour tile: its alpha."""
    return np.asarray(Image.open(io.BytesIO(data)).convert("RGBA").getchannel("A")) > 64


# Layers where an empty tile can mean a failed render. Woods & water is empty over every field and town.
BLANK_CHECKED = ("contours", "hillshade")
BLANK_MAX_BYTES = 2048   # a fully transparent 256 px tile PNG is a few hundred bytes; anything with content is more


def _is_blank(layer: str, data: bytes) -> bool:
    """A contour tile with no lines / a hillshade tile with no data (fully transparent)."""
    if layer not in BLANK_CHECKED or len(data) > BLANK_MAX_BYTES:
        return False
    try:
        im = Image.open(io.BytesIO(data))
        alpha = im.getchannel("A") if "A" in im.getbands() else None
    except Exception:
        return False
    return alpha is not None and alpha.getextrema()[1] == 0


BLANK_SWEEP_MARKER = ".blank-check-v2"   # v1 only caught wholly blank tiles; v2 also holes inside contour blocks


def _remove(paths) -> int:
    n = 0
    for p in paths:
        try:
            os.remove(p)
            n += 1
        except OSError:
            pass
    return n


def _block_hole_score(tiles: dict) -> Optional[int]:
    """Hole score of a cached contour block from its 16 tile files ({(x, y): path}); None if one is unreadable."""
    side = META * TILE_PX
    ink = np.zeros((side, side), bool)
    x0 = min(x for x, _ in tiles)
    y0 = min(y for _, y in tiles)
    try:
        for (x, y), path in tiles.items():
            with open(path, "rb") as f:
                t = _cached_ink(f.read())
            if t.shape != (TILE_PX, TILE_PX):
                return None
            ink[(y - y0) * TILE_PX:(y - y0 + 1) * TILE_PX, (x - x0) * TILE_PX:(x - x0 + 1) * TILE_PX] = t
    except Exception:
        return None
    return hole_score(ink)


def recheck_blank_tiles() -> Optional[dict]:
    """One-time repair of tiles cached before blocks were checked, so they're fetched (and checked) again when next
    viewed: deletes every tile of a fully cached contour block with a hole in it, and each blank contour/hillshade
    tile whose 4x4 block has a cached tile with content (wholly blank blocks are flat land, left alone).
    Returns {"blank": tiles deleted, "checked": contour blocks scored, "holed": contour blocks deleted}, or None when
    the marker says it has already run."""
    marker = os.path.join(TILE_CACHE_DIR, BLANK_SWEEP_MARKER)
    if os.path.exists(marker) or not os.path.isdir(TILE_CACHE_DIR):
        return None
    out = {"blank": 0, "checked": 0, "holed": 0}
    for layer in BLANK_CHECKED:
        layer_dir = os.path.join(TILE_CACHE_DIR, layer)
        if not os.path.isdir(layer_dir):
            continue
        for z in os.listdir(layer_dir):
            z_dir = os.path.join(layer_dir, z)
            blocks: dict[tuple, dict] = {}     # (x0, y0) -> {(x, y): (path, blank?)}
            for x in os.listdir(z_dir) if os.path.isdir(z_dir) else []:
                x_dir = os.path.join(z_dir, x)
                if not (x.isdigit() and os.path.isdir(x_dir)):
                    continue
                for name in os.listdir(x_dir):
                    y = name[:-4]
                    if not (name.endswith(".png") and y.isdigit()):
                        continue
                    path = os.path.join(x_dir, name)
                    try:
                        blank = False
                        if os.path.getsize(path) <= BLANK_MAX_BYTES:
                            with open(path, "rb") as f:
                                blank = _is_blank(layer, f.read())
                    except OSError:
                        continue
                    blocks.setdefault(block_of(int(x), int(y)), {})[(int(x), int(y))] = (path, blank)
            for (bx, by), tiles in blocks.items():
                flags = [b for _, b in tiles.values()]
                if layer == "contours" and len(tiles) == META * META and not all(flags):
                    out["checked"] += 1
                    score = _block_hole_score({k: p for k, (p, _) in tiles.items()})
                    if score is not None and score >= HOLE_SCORE:
                        log.info("tiles: cached contours/%s/%s/%s has a hole (score %d), removing the block",
                                 z, bx, by, score)
                        _remove(p for p, _ in tiles.values())
                        out["holed"] += 1
                        continue
                if any(flags) and not all(flags):
                    out["blank"] += _remove(p for p, b in tiles.values() if b)
    try:
        with open(marker, "w") as f:
            f.write(f"{time.time():.0f}\n")
    except OSError:
        log.warning("could not write %s", marker, exc_info=True)
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
