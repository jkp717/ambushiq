"""Ruler elevation profile: sampling, climb/drop/net, the property-grid fallback and the cache (no network)."""
import asyncio

import numpy as np
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from app.dependencies import get_active_region_id, require_token
from app.elevation import router as elev_router
from app.elevation import service
from app.forecast.eddy import Grid

A, B = (37.5000, -88.7000), (37.5000, -88.6900)     # ~880 m east-west


@pytest.fixture(autouse=True)
def clear_cache():
    service._cache.clear()
    yield
    service._cache.clear()


def test_sampling_is_about_ten_metres_and_clamped():
    pts = service.sample_points(A, B)
    assert pts[0] == A and pts[-1] == B
    assert 85 <= len(pts) <= 92
    assert len(service.sample_points(A, (37.50001, -88.70001))) == service.MIN_SAMPLES
    assert len(service.sample_points(A, (37.6, -88.5))) == service.MAX_SAMPLES


def test_climb_drop_and_net_ignore_noise():
    up = list(np.linspace(100, 130, 31))
    down = list(np.linspace(130, 120, 11))[1:]
    noisy = [v + (0.2 if i % 2 else -0.2) for i, v in enumerate([120.0] * 10)]
    out = service.summarize(up + down + noisy)
    ft = lambda m: round(m * service.M_TO_FT)
    assert out["net_ft"] == ft(120.0 - 100.0)
    assert abs(out["gain_ft"] - ft(30)) <= 2 and abs(out["loss_ft"] - ft(10)) <= 2


def _fake_usgs(monkeypatch, calls, fail=False):
    async def fetch(points):
        calls.append(len(points))
        if fail:
            raise service.ElevationUnavailable("down")
        return [100.0 + 10.0 * i / (len(points) - 1) for i in range(len(points))]
    monkeypatch.setattr(service, "_fetch_usgs", fetch)


def test_profile_from_usgs_and_cached(monkeypatch):
    calls = []
    _fake_usgs(monkeypatch, calls)
    out = asyncio.run(service.profile(A, B))
    assert out["source"] == "USGS 3DEP" and out["net_ft"] == round(10 * service.M_TO_FT)
    assert out["loss_ft"] == 0
    asyncio.run(service.profile(A, B))
    assert len(calls) == 1


def test_usgs_down_falls_back_to_the_property_grid(monkeypatch):
    _fake_usgs(monkeypatch, [], fail=True)
    # a grid covering the line whose elevation rises 1 m per column eastward
    dem = np.tile(np.arange(50, dtype=np.float64), (50, 1))
    grid = Grid(dem=dem, cell_m=40, north=37.51, south=37.49, west=-88.71, east=-88.68, key=(1, "t"), source="USGS 3DEP")
    monkeypatch.setattr(service.region_terrain, "load_grid", lambda region_id: grid)
    out = asyncio.run(service.profile(A, B, region_id=1))
    assert out["source"] == "property terrain grid" and out["net_ft"] > 0 and out["loss_ft"] == 0


def test_usgs_down_and_no_grid_is_a_503(monkeypatch):
    _fake_usgs(monkeypatch, [], fail=True)
    monkeypatch.setattr(service.region_terrain, "load_grid", lambda region_id: None)
    app = FastAPI()
    app.include_router(elev_router.router)
    app.dependency_overrides[get_active_region_id] = lambda: 1
    app.dependency_overrides[require_token] = lambda: None
    r = TestClient(app).get("/api/elevation/profile", params={"a_lat": A[0], "a_lon": A[1], "b_lat": B[0], "b_lon": B[1]})
    assert r.status_code == 503
