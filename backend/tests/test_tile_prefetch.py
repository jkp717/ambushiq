"""Overnight terrain-tile prefetch: block planning, the cache check, queued requests and the nightly job (no network)."""
import asyncio
import os
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

import app.main  # noqa: F401  (imports every router/model so create_all sees all tables)
from app.core.database import Base, engine
from app.jobs import service as jobs
from app.regions.models import Region
from app.tiles import prefetch, service
from app.tiles import router as tiles_router
from app.tiles.models import TilePrefetchArea

LAT0, LON0 = 34.7, -92.3
# a small view: ~550 m square
VIEW = (LAT0 + 0.0025, LAT0 - 0.0025, LON0 - 0.003, LON0 + 0.003)


@pytest.fixture()
def cache_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(service, "TILE_CACHE_DIR", str(tmp_path))
    return tmp_path


@pytest.fixture()
def db():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    jobs._running.clear()
    with Session(engine) as s:
        s.add(Region(id=1, name="Home", lat=LAT0, lon=LON0, rut_peak_month=12, rut_peak_day=5,
                     property_timezone="America/Chicago", is_default=1, created_at=""))
        s.commit()


@pytest.fixture()
def fetches(monkeypatch):
    """get_tile stand-in that 'fetches' a block by writing its 16 tiles; records each call."""
    calls = []

    async def fake_get_tile(layer, z, x, y, still_wanted=None, background=False):
        calls.append((layer, z, x, y, background))
        for tx, ty in service.block_tiles(x, y):
            path = service._cache_path(layer, z, tx, ty)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "wb") as f:
                f.write(b"png")
        return b"png"

    monkeypatch.setattr(service, "get_tile", fake_get_tile)
    return calls


def _cache_block(layer, z, x0, y0, age_s=0):
    for x, y in service.block_tiles(x0, y0):
        path = service._cache_path(layer, z, x, y)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            f.write(b"png")
        if age_s:
            t = time.time() - age_s
            os.utime(path, (t, t))


def test_blocks_cover_the_view_at_each_rendered_zoom():
    blocks = prefetch.blocks_for(VIEW, "contours", 0, 99)
    zooms = sorted({z for _, z, _, _ in blocks})
    assert zooms == list(range(13, 19))
    assert all(x0 % service.META == 0 and y0 % service.META == 0 for _, _, x0, y0 in blocks)
    # every tile of the view's corners at z18 is inside some block
    north, south, west, east = VIEW
    for lat, lon in ((north, west), (south, east)):
        x, y = prefetch._tile_xy(lat, lon, 18)
        assert ("contours", 18, *service.block_of(x, y)) in blocks


def test_zoom_range_is_clipped_to_the_layer_and_the_request():
    assert {z for _, z, _, _ in prefetch.blocks_for(VIEW, "hillshade", 5, 10)} == {8, 9, 10}
    assert prefetch.plan(VIEW, ["landcover"], 8, 15) == []       # woods & water isn't prefetched


def test_check_reports_missing_blocks_then_ready(cache_dir):
    blocks = prefetch.plan(VIEW, ["contours"], 17, 18)
    first = prefetch.check(VIEW, ["contours"], 17, 18)
    assert first["ready"] is False and first["missing_blocks"] == first["total_blocks"] == len(blocks)
    assert first["est_minutes"] >= 1
    for b in blocks:
        _cache_block(*b)
    assert prefetch.check(VIEW, ["contours"], 17, 18) == {"ready": True, "total_blocks": len(blocks),
                                                          "missing_blocks": 0, "est_minutes": 0}


def test_check_refuses_a_huge_area(cache_dir):
    with pytest.raises(ValueError):
        prefetch.check((36.0, 34.0, -94.0, -91.0), ["contours"], 18, 18)


def test_cached_blocks_are_touched_and_missing_ones_fetched_in_background(cache_dir, fetches):
    blocks = prefetch.plan(VIEW, ["contours"], 18, 18)
    old = blocks[0]
    _cache_block(*old, age_s=10 * 86400)
    t = asyncio.run(prefetch.prefetch_blocks(blocks, time.monotonic() + 60))
    assert t.cached == 1 and t.fetched == len(blocks) - 1 and t.failed == 0
    assert all(bg for *_, bg in fetches) and old[:2] + old[2:] not in [c[:4] for c in fetches]
    path = service._cache_path(old[0], old[1], old[2], old[3])
    assert time.time() - os.path.getmtime(path) < 60          # touched so the prune keeps it


def test_failed_blocks_are_counted(cache_dir, monkeypatch):
    async def failing(layer, z, x, y, still_wanted=None, background=False):
        raise service.UpstreamError("HTTP 504")

    monkeypatch.setattr(service, "get_tile", failing)
    blocks = prefetch.plan(VIEW, ["contours"], 18, 18)
    t = asyncio.run(prefetch.prefetch_blocks(blocks, time.monotonic() + 60))
    assert t.failed == len(blocks) and t.done == 0 and t.failures


def test_deadline_stops_the_run(cache_dir, fetches):
    t = asyncio.run(prefetch.prefetch_blocks(prefetch.plan(VIEW, ["contours"], 18, 18), time.monotonic() - 1))
    assert t.timed_out and fetches == []


def test_queue_reuses_an_unfinished_request_that_covers_it(db, cache_dir):
    a = prefetch.queue_area(VIEW, ["contours", "hillshade"], 13, 18, 1, "Creek")
    inner = (VIEW[0] - 0.001, VIEW[1] + 0.001, VIEW[2] + 0.001, VIEW[3] - 0.001)
    b = prefetch.queue_area(inner, ["contours"], 15, 18, 1, "Inner")
    assert b["id"] == a["id"] and a["status"] == "queued" and a["layers"] == ["contours", "hillshade"]
    wider = (VIEW[0] + 0.01, VIEW[1], VIEW[2], VIEW[3])
    assert prefetch.queue_area(wider, ["contours"], 15, 18, 1, "Wider")["id"] != a["id"]
    assert [r["label"] for r in prefetch.list_areas(1)] == ["Wider", "Creek"]


def test_queue_needs_a_terrain_layer(db):
    with pytest.raises(ValueError):
        prefetch.queue_area(VIEW, ["landcover"], 8, 15, 1, "x")


def test_region_box_comes_from_the_property_box(db):
    from app.regions import terrain as rt
    [(name, (north, south, west, east))] = prefetch._region_boxes()
    assert name == "Home"
    half_lat = (rt.MIN_BOX_M / 2) / rt.M_PER_DEG_LAT        # no stands: a MIN_BOX_M square on the region point
    assert north == pytest.approx(LAT0 + half_lat) and south == pytest.approx(LAT0 - half_lat)
    assert west < LON0 < east


def test_nightly_job_finishes_queued_areas_then_region_boxes(db, cache_dir, fetches, monkeypatch):
    monkeypatch.setattr(prefetch, "_region_boxes", lambda: [("Home", VIEW)])
    area = prefetch.queue_area(VIEW, ["contours"], 17, 18, 1, "Creek")
    asyncio.run(jobs.run_tracked("prefetch_tiles", "manual"))
    run = next(j for j in jobs.job_list(None) if j["id"] == "prefetch_tiles")["last_run"]
    assert run["status"] == "success"
    with Session(engine) as s:
        a = s.get(TilePrefetchArea, area["id"])
        assert a.status == "ready" and a.blocks_done == a.blocks_total and a.finished_at
    # region box: contours z13-18 + hillshade z8-18; the queued area's z17-18 contours were already fetched
    region_blocks = prefetch.plan(VIEW, prefetch.PREFETCH_LAYERS, 0, 99)
    assert len(fetches) == len(region_blocks)
    assert prefetch.check(VIEW, prefetch.PREFETCH_LAYERS, 0, 99)["ready"]


def test_a_failed_area_stays_queued_for_the_next_night(db, cache_dir, monkeypatch):
    async def failing(layer, z, x, y, still_wanted=None, background=False):
        raise service.UpstreamError("HTTP 502")

    monkeypatch.setattr(service, "get_tile", failing)
    monkeypatch.setattr(prefetch, "_region_boxes", lambda: [])
    area = prefetch.queue_area(VIEW, ["contours"], 18, 18, 1, "Creek")
    asyncio.run(jobs.run_tracked("prefetch_tiles", "manual"))
    with Session(engine) as s:
        a = s.get(TilePrefetchArea, area["id"])
        assert a.status == "partial" and a.blocks_failed == a.blocks_total and not a.finished_at


def test_check_endpoint_and_validation(cache_dir):
    # (queue/list touch the DB from a worker thread, which the tests' in-memory SQLite can't share; they're
    # covered through prefetch.queue_area / list_areas above)
    app_ = FastAPI()
    app_.include_router(tiles_router.router)
    c = TestClient(app_)
    body = {"north": VIEW[0], "south": VIEW[1], "west": VIEW[2], "east": VIEW[3],
            "layers": ["contours"], "min_zoom": 18, "max_zoom": 18}
    r = c.post("/api/tiles/prefetch/check", json=body)
    assert r.status_code == 200 and r.json()["ready"] is False
    assert c.post("/api/tiles/prefetch/check", json={**body, "north": VIEW[1] - 1}).status_code == 422
    assert c.post("/api/tiles/prefetch", json={**body, "min_zoom": 18, "max_zoom": 15}).status_code == 422
    huge = {**body, "north": 36.0, "south": 34.0, "west": -94.0, "east": -91.0}
    assert c.post("/api/tiles/prefetch/check", json=huge).status_code == 422
