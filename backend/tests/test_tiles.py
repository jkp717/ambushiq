"""Terrain overlay tiles: tile math, recoloring, the disk cache and the route's error handling (no network)."""
import asyncio
import io
import math
import os
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

from app.tiles import router as tiles_router
from app.tiles import service


BLOCK_PX = service.META * service.TILE_PX   # one upstream image: META x META tiles


def _png(im: Image.Image) -> bytes:
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    return buf.getvalue()


def _open(data: bytes) -> Image.Image:
    return Image.open(io.BytesIO(data)).convert("RGBA")


@pytest.fixture()
def cache_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(service, "TILE_CACHE_DIR", str(tmp_path))
    return tmp_path


@pytest.fixture()
def client():
    app = FastAPI()
    app.include_router(tiles_router.router)
    return TestClient(app)


def test_tile_bbox_world_and_quadrant():
    half = 6378137.0 * math.pi
    assert service.tile_bbox_3857(0, 0, 0) == pytest.approx((-half, -half, half, half))
    # z1 x1 y0 is the north-east quarter
    assert service.tile_bbox_3857(1, 1, 0) == pytest.approx((0, 0, half, half))


@pytest.mark.parametrize("layer,z,x,y,ok", [
    ("contours", 16, 15000, 25000, True),
    ("contours", 12, 100, 100, False),      # below the contour zoom range
    ("hillshade", 19, 0, 0, False),         # above the range
    ("landcover", 10, 1024, 0, False),      # x off the grid
    ("streets", 10, 0, 0, False),           # unknown layer
])
def test_tile_in_range(layer, z, x, y, ok):
    assert service.tile_in_range(layer, z, x, y) is ok


def test_recolor_contours_transparent_background_colored_lines():
    im = Image.new("L", (4, 1), 253)
    im.putpixel((1, 0), 0)
    out = _open(service.recolor_contours(_png(im)))
    assert out.getpixel((0, 0))[3] == 0
    r, g, b, a = out.getpixel((1, 0))
    assert (r, g, b) == service.CONTOUR_RGB and a == 255


def test_recolor_landcover_keeps_woods_and_water_only():
    forest, water, crops = (28, 99, 48), (71, 107, 160), (171, 108, 40)
    im = Image.new("RGBA", (30, 10))
    for x in range(30):
        for y in range(10):
            im.putpixel((x, y), (forest if x < 10 else water if x < 20 else crops) + (255,))
    out = _open(service.recolor_landcover(_png(im)))
    assert out.getpixel((4, 5))[:3] == service.NLCD_TINTS[forest] and out.getpixel((4, 5))[3] == 255
    assert out.getpixel((15, 5))[:3] == service.NLCD_TINTS[water]
    assert out.getpixel((26, 5))[3] == 0


def test_get_tile_fetches_once_then_serves_cache(cache_dir, monkeypatch, client):
    calls = []

    async def fake_fetch(layer, z, x, y, still_wanted=None):
        calls.append((layer, z, x, y))
        return _png(Image.new("L", (BLOCK_PX, BLOCK_PX), 200))

    monkeypatch.setattr(service, "_fetch", fake_fetch)
    first = client.get("/api/tiles/hillshade/10/200/300.png")
    second = client.get("/api/tiles/hillshade/10/200/300.png")
    assert first.status_code == second.status_code == 200
    assert first.content == second.content
    assert len(calls) == 1
    assert "max-age" in first.headers["cache-control"]
    assert os.path.isfile(cache_dir / "hillshade" / "10" / "200" / "300.png")


def test_upstream_failure_is_an_uncached_503(cache_dir, monkeypatch, client):
    async def failing_fetch(layer, z, x, y, still_wanted=None):
        raise service.UpstreamError("down")

    monkeypatch.setattr(service, "_fetch", failing_fetch)
    r = client.get("/api/tiles/contours/15/100/100.png")
    # a failure, not a blank tile, so the map retries it instead of keeping a hole
    assert r.status_code == 503 and r.headers["cache-control"] == "no-store"
    assert r.content == b""
    assert not any(cache_dir.rglob("*.png"))


class _Resp:
    def __init__(self, status=200):
        self.status_code = status
        self.headers = {"content-type": "image/png" if status == 200 else "text/html"}
        self.content = _png(Image.new("L", (BLOCK_PX, BLOCK_PX), 180)) if status == 200 else b"<html>"


def _fake_upstream(monkeypatch, calls, statuses=(), delay=0.0):
    """Upstream that answers with `statuses` in turn (then 200s), after `delay` seconds each."""
    queue = list(statuses)

    class FakeClient:
        async def get(self, url, params=None):
            calls.append(params)
            if delay:
                await asyncio.sleep(delay)
            return _Resp(queue.pop(0) if queue else 200)

    monkeypatch.setattr(service, "_client", FakeClient())
    monkeypatch.setattr(service, "_sem", None)
    monkeypatch.setattr(service, "RETRY_DELAY_S", 0)


def _answer(value):
    async def check():
        return value
    return check


def test_abandoned_tile_skips_upstream_and_cache(cache_dir, monkeypatch):
    calls = []
    _fake_upstream(monkeypatch, calls)
    with pytest.raises(service.TileAbandoned):
        asyncio.run(service.get_tile("hillshade", 12, 1000, 1500, _answer(False)))
    assert calls == []
    assert not any(cache_dir.rglob("*.png"))


def test_cached_tile_served_even_if_abandoned(cache_dir, monkeypatch):
    calls = []
    _fake_upstream(monkeypatch, calls)
    first = asyncio.run(service.get_tile("contours", 15, 100, 100, _answer(True)))
    again = asyncio.run(service.get_tile("contours", 15, 100, 100, _answer(False)))
    assert first == again and len(calls) == 1
    assert os.path.isfile(cache_dir / "contours" / "15" / "100" / "100.png")


def test_gateway_error_is_retried_once(cache_dir, monkeypatch):
    calls = []
    _fake_upstream(monkeypatch, calls, statuses=[504])
    data = asyncio.run(service.get_tile("contours", 16, 16628, 25404))
    assert data and len(calls) == 2
    assert os.path.isfile(cache_dir / "contours" / "16" / "16628" / "25404.png")


def test_two_gateway_errors_give_up(cache_dir, monkeypatch):
    calls = []
    _fake_upstream(monkeypatch, calls, statuses=[502, 504])
    with pytest.raises(service.UpstreamError):
        asyncio.run(service.get_tile("contours", 16, 16628, 25404))
    assert len(calls) == 2 and not any(cache_dir.rglob("*.png"))


def test_client_error_is_not_retried(cache_dir, monkeypatch):
    calls = []
    _fake_upstream(monkeypatch, calls, statuses=[400])
    with pytest.raises(service.UpstreamError):
        asyncio.run(service.get_tile("contours", 16, 16628, 25404))
    assert len(calls) == 1


def test_concurrent_requests_share_one_fetch(cache_dir, monkeypatch):
    calls = []
    _fake_upstream(monkeypatch, calls, delay=0.05)

    async def both():
        return await asyncio.gather(service.get_tile("hillshade", 12, 1000, 1500, _answer(True)),
                                    service.get_tile("hillshade", 12, 1000, 1500, _answer(True)))

    a, b = asyncio.run(both())
    assert a == b and len(calls) == 1
    assert service._inflight == {}


def test_shared_fetch_goes_ahead_if_any_waiter_still_wants_it(cache_dir, monkeypatch):
    calls = []
    _fake_upstream(monkeypatch, calls)

    async def both():
        # the queue is full while both requests arrive; by the time a slot frees, one has given up
        service._sem = asyncio.Semaphore(1)
        await service._sem.acquire()
        reqs = asyncio.gather(service.get_tile("hillshade", 12, 1000, 1500, _answer(False)),
                              service.get_tile("hillshade", 12, 1000, 1500, _answer(True)))
        await asyncio.sleep(0.2)
        service._sem.release()
        return await reqs

    a, b = asyncio.run(both())
    assert a == b and len(calls) == 1


def test_block_bbox_spans_its_tiles():
    x0, y0 = service.block_of(16630, 25407)
    assert (x0, y0) == (16628, 25404)
    minx, miny, maxx, maxy = service.block_bbox_3857(16, x0, y0)
    tl = service.tile_bbox_3857(16, x0, y0)
    br = service.tile_bbox_3857(16, x0 + service.META - 1, y0 + service.META - 1)
    assert (minx, maxy) == (tl[0], tl[3]) and (miny, maxx) == (br[1], br[2])


def test_one_upstream_request_fills_the_whole_block(cache_dir, monkeypatch):
    calls = []
    _fake_upstream(monkeypatch, calls)
    asyncio.run(service.get_tile("contours", 16, 16630, 25407))
    assert len(calls) == 1 and calls[0]["size"] == f"{BLOCK_PX},{BLOCK_PX}"
    tiles = sorted(cache_dir.rglob("*.png"))
    assert len(tiles) == service.META ** 2
    assert all(_open(p.read_bytes()).size == (service.TILE_PX, service.TILE_PX) for p in tiles)
    # a neighbour in the same block is now a cache hit
    asyncio.run(service.get_tile("contours", 16, 16628, 25404))
    assert len(calls) == 1


def test_block_is_cut_in_the_right_places(cache_dir, monkeypatch):
    # an upstream image whose left half is dark and right half light: only the left tiles get contour ink
    im = Image.new("L", (BLOCK_PX, BLOCK_PX), 253)
    im.paste(0, (0, 0, BLOCK_PX // 2, BLOCK_PX))

    async def fake_fetch(layer, z, x, y, still_wanted=None):
        return _png(im)

    monkeypatch.setattr(service, "_fetch", fake_fetch)
    left = _open(asyncio.run(service.get_tile("contours", 16, 16628, 25404)))
    right = _open(asyncio.run(service.get_tile("contours", 16, 16631, 25404)))
    assert left.getpixel((10, 10))[3] == 255 and right.getpixel((10, 10))[3] == 0


def test_requests_for_different_tiles_of_one_block_share_a_fetch(cache_dir, monkeypatch):
    calls = []
    _fake_upstream(monkeypatch, calls, delay=0.05)

    async def both():
        return await asyncio.gather(service.get_tile("hillshade", 12, 1000, 1500),
                                    service.get_tile("hillshade", 12, 1003, 1503))

    a, b = asyncio.run(both())
    assert a and b and len(calls) == 1


def test_out_of_range_is_404(client):
    assert client.get("/api/tiles/contours/5/0/0.png").status_code == 404
    assert client.get("/api/tiles/nope/10/0/0.png").status_code == 404


def test_prune_cache_removes_only_old_tiles(cache_dir):
    old = cache_dir / "contours" / "15" / "1" / "1.png"
    new = cache_dir / "contours" / "15" / "1" / "2.png"
    old.parent.mkdir(parents=True)
    old.write_bytes(b"x")
    new.write_bytes(b"x")
    stale = time.time() - service.PRUNE_AFTER_S - 10
    os.utime(old, (stale, stale))
    assert service.prune_cache() == 1
    assert not old.exists() and new.exists()


# ---------- blocks that come back partly blank ----------

def _block(dark_cols: int) -> bytes:
    """A contour block image with ink in its first `dark_cols` tile columns (0 = all blank)."""
    im = Image.new("L", (BLOCK_PX, BLOCK_PX), 253)
    if dark_cols:
        im.paste(0, (0, 0, dark_cols * service.TILE_PX, BLOCK_PX))
    return _png(im)


def _scripted_fetch(monkeypatch, answers, calls):
    async def fake_fetch(layer, z, x, y, still_wanted=None):
        calls.append(layer)
        a = answers[min(len(calls), len(answers)) - 1]
        if isinstance(a, Exception):
            raise a
        return a
    monkeypatch.setattr(service, "_fetch", fake_fetch)
    monkeypatch.setattr(service, "RETRY_DELAY_S", 0)


def _ink(data: bytes) -> bool:
    return _open(data).getchannel("A").getextrema()[1] > 0


def test_partly_blank_block_is_fetched_again_and_filled(cache_dir, monkeypatch):
    calls = []
    _scripted_fetch(monkeypatch, [_block(2), _block(4)], calls)
    right = asyncio.run(service.get_tile("contours", 16, 16631, 25404))   # blank in the first answer
    assert len(calls) == 2 and _ink(right)
    assert _ink((cache_dir / "contours" / "16" / "16631" / "25404.png").read_bytes())


def test_wholly_blank_block_is_fetched_once(cache_dir, monkeypatch):
    calls = []
    _scripted_fetch(monkeypatch, [_block(0)], calls)
    data = asyncio.run(service.get_tile("contours", 16, 16628, 25404))
    assert len(calls) == 1 and not _ink(data)
    assert len(list(cache_dir.rglob("*.png"))) == service.META ** 2


def test_fully_drawn_block_is_fetched_once(cache_dir, monkeypatch):
    calls = []
    _scripted_fetch(monkeypatch, [_block(4)], calls)
    asyncio.run(service.get_tile("contours", 16, 16628, 25404))
    assert len(calls) == 1


def test_failed_refetch_keeps_the_first_answer(cache_dir, monkeypatch):
    calls = []
    _scripted_fetch(monkeypatch, [_block(2), service.UpstreamError("HTTP 504")], calls)
    left = asyncio.run(service.get_tile("contours", 16, 16628, 25404))
    assert len(calls) == 2 and _ink(left)
    assert len(list(cache_dir.rglob("*.png"))) == service.META ** 2


def test_landcover_blocks_are_never_refetched(cache_dir, monkeypatch):
    calls = []
    half = Image.new("RGB", (BLOCK_PX, BLOCK_PX), (255, 255, 255))
    half.paste((104, 170, 99), (0, 0, BLOCK_PX // 2, BLOCK_PX))
    _scripted_fetch(monkeypatch, [_png(half)], calls)
    asyncio.run(service.get_tile("landcover", 12, 1000, 1500))
    assert len(calls) == 1


def _cache_tile(cache_dir, z, x, y, ink: bool):
    p = cache_dir / "contours" / str(z) / str(x) / f"{y}.png"
    p.parent.mkdir(parents=True, exist_ok=True)
    im = Image.new("L", (service.TILE_PX, service.TILE_PX), 0 if ink else 253)
    p.write_bytes(service.recolor_contours(im))
    return p


def test_sweep_removes_blank_tiles_beside_drawn_ones_once(cache_dir):
    drawn = _cache_tile(cache_dir, 16, 16628, 25404, ink=True)
    hole = _cache_tile(cache_dir, 16, 16629, 25404, ink=False)
    flat = [_cache_tile(cache_dir, 16, 16632 + i, 25404, ink=False) for i in range(2)]   # wholly blank block
    assert service.recheck_blank_tiles()["blank"] == 1
    assert drawn.exists() and not hole.exists() and all(p.exists() for p in flat)
    # runs once: later blanks (now checked at fetch time) are left alone
    hole = _cache_tile(cache_dir, 16, 16629, 25404, ink=False)
    assert service.recheck_blank_tiles() is None and hole.exists()


# ---------- blocks with a straight-edged hole in the contours ----------

def _rings(hole: bool = False) -> Image.Image:
    """A synthetic contour block: groups of concentric rings (dark lines on white), optionally with a blank
    rectangle cut out of the middle the way 3DEP sometimes leaves one."""
    from PIL import ImageDraw
    im = Image.new("L", (BLOCK_PX, BLOCK_PX), 253)
    d = ImageDraw.Draw(im)
    for cx, cy in ((250, 250), (760, 300), (300, 780), (780, 760), (512, 512)):
        for r in range(10, 330, 14):
            d.ellipse((cx - r, cy - r, cx + r, cy + r), outline=0, width=2)
    if hole:
        d.rectangle((400, 300, 800, 600), fill=253)
    return im


def test_hole_score_separates_clean_and_holed_blocks():
    clean = service.hole_score(service._upstream_ink(_rings()))
    holed = service.hole_score(service._upstream_ink(_rings(hole=True)))
    assert clean < service.HOLE_SCORE <= holed


def test_holed_block_is_fetched_again_and_the_clean_one_cached(cache_dir, monkeypatch):
    calls = []
    _scripted_fetch(monkeypatch, [_png(_rings(hole=True)), _png(_rings())], calls)
    data = asyncio.run(service.get_tile("contours", 16, 16629, 25405))   # a tile inside the hole
    assert len(calls) == 2 and _ink(data)
    cached = {p: p.read_bytes() for p in cache_dir.rglob("*.png")}
    assert len(cached) == service.META ** 2


def test_block_holed_every_time_keeps_the_best_attempt(cache_dir, monkeypatch):
    calls = []
    _scripted_fetch(monkeypatch, [_png(_rings(hole=True))], calls)
    asyncio.run(service.get_tile("contours", 16, 16628, 25404))
    assert len(calls) == 1 + service.HOLE_RETRIES
    assert len(list(cache_dir.rglob("*.png"))) == service.META ** 2


def test_clean_contour_block_is_fetched_once(cache_dir, monkeypatch):
    calls = []
    _scripted_fetch(monkeypatch, [_png(_rings())], calls)
    asyncio.run(service.get_tile("contours", 16, 16628, 25404))
    assert len(calls) == 1


def _cache_block(cache_dir, z, x0, y0, im: Image.Image, skip=()):
    tiles, _ = service._split("contours", x0, y0, _png(im))
    paths = []
    for (x, y), data in tiles.items():
        if (x, y) in skip:
            continue
        p = cache_dir / "contours" / str(z) / str(x) / f"{y}.png"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        paths.append(p)
    return paths


def test_sweep_removes_cached_blocks_with_holes(cache_dir):
    holed = _cache_block(cache_dir, 16, 16624, 25392, _rings(hole=True))
    clean = _cache_block(cache_dir, 16, 16628, 25392, _rings())
    partial = _cache_block(cache_dir, 16, 16632, 25392, _rings(hole=True), skip={(16632, 25392)})
    r = service.recheck_blank_tiles()
    assert r == {"blank": 0, "checked": 2, "holed": 1}
    assert not any(p.exists() for p in holed)
    assert all(p.exists() for p in clean + partial)     # incomplete blocks aren't scored
    assert service.recheck_blank_tiles() is None
