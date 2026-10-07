"""Terrain overlay tiles: tile math, recoloring, the disk cache and the route's error handling (no network)."""
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

    async def fake_fetch(layer, z, x, y):
        calls.append((layer, z, x, y))
        return _png(Image.new("L", (256, 256), 200))

    monkeypatch.setattr(service, "_fetch", fake_fetch)
    first = client.get("/api/tiles/hillshade/10/200/300.png")
    second = client.get("/api/tiles/hillshade/10/200/300.png")
    assert first.status_code == second.status_code == 200
    assert first.content == second.content
    assert len(calls) == 1
    assert "max-age" in first.headers["cache-control"]
    assert os.path.isfile(cache_dir / "hillshade" / "10" / "200" / "300.png")


def test_upstream_failure_returns_blank_uncached(cache_dir, monkeypatch, client):
    async def failing_fetch(layer, z, x, y):
        raise service.UpstreamError("down")

    monkeypatch.setattr(service, "_fetch", failing_fetch)
    r = client.get("/api/tiles/contours/15/100/100.png")
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
    assert _open(r.content).getextrema()[3] == (0, 0)
    assert not any(cache_dir.rglob("*.png"))


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
