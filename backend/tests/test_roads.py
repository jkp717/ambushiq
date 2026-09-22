import asyncio
import math
import time

import httpx
import pytest
from fastapi import HTTPException
from sqlalchemy import delete
from sqlalchemy.orm import Session

import app.main  # noqa: F401  (registers every model so create_all sees all tables)
from app.core.database import Base, engine
from app.roads import router as roads_router, service
from app.roads.models import RoadCell

BOX = dict(south=34.69, west=-92.31, north=34.71, east=-92.29, zoom=13)        # inside one grid cell
WIDE = dict(south=34.60, west=-92.40, north=34.80, east=-92.20, zoom=13)       # spans several cells


def line(x=-92.30, y=34.70, n=3, dx=0.001):
    return {"type": "LineString", "coordinates": [[x + i * dx, y] for i in range(n)]}


def road_feature(fid, name="Some Road", geom=None):
    return {"type": "Feature", "id": fid, "properties": {"name": name},
            "geometry": geom if geom is not None else line()}


@pytest.fixture()
def env(monkeypatch):
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        s.execute(delete(RoadCell))
        s.commit()
    service._mem.clear()
    service._inflight.clear()
    monkeypatch.setattr(service.asyncio, "sleep", lambda *_a, **_k: _instant())
    state = {"requests": {}, "features": {"highway": [], "secondary": [], "connector": [], "local": [road_feature(1)]},
             "status": 200, "pages": None}

    def handler(request):
        layer_id = int(request.url.path.rstrip("/query").rsplit("/", 1)[-1])
        kind = next(k for k, lid in service._LAYER_IDS.items() if lid == layer_id)
        state["requests"].setdefault(kind, []).append(dict(request.url.params))
        if state["status"] != 200:
            return httpx.Response(state["status"], json={})
        if state["pages"] is not None and kind == "local":
            off = int(request.url.params.get("resultOffset", 0)) // int(request.url.params["resultRecordCount"])
            page = state["pages"][off]
            return httpx.Response(200, json={"type": "FeatureCollection", "features": page,
                                             "properties": {"exceededTransferLimit": off < len(state["pages"]) - 1}})
        return httpx.Response(200, json={"type": "FeatureCollection", "features": state["features"].get(kind, [])})

    real = httpx.AsyncClient
    monkeypatch.setattr(service.httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    return state


async def _instant():
    return None


def run(**box):
    return asyncio.run(service.roads(**box))


# ---------- validation and grid ----------

def test_bad_requests_are_rejected():
    with pytest.raises(service.BadBounds):
        run(**{**BOX, "zoom": 10})                                              # too far out
    with pytest.raises(service.BadBounds):
        run(**{**BOX, "south": 35.0, "north": 34.0})                            # inverted
    with pytest.raises(service.BadBounds):
        run(south=30, west=-100, north=40, east=-80, zoom=12)                   # far too many cells


def test_router_maps_bad_bounds_and_outages_to_http_errors(env):
    with pytest.raises(HTTPException) as bad:
        asyncio.run(roads_router.roads(south=1, west=1, north=2, east=2, zoom=5, _=None))
    assert bad.value.status_code == 400
    env["status"] = 500
    with pytest.raises(HTTPException) as down:
        asyncio.run(roads_router.roads(**BOX, _=None))
    assert down.value.status_code == 502


def test_grid_cells_cover_the_box_without_gaps():
    tz = service.tile_zoom(13)
    assert tz == 12
    one = service.cells_for_bbox(BOX["south"], BOX["west"], BOX["north"], BOX["east"], tz)
    many = service.cells_for_bbox(WIDE["south"], WIDE["west"], WIDE["north"], WIDE["east"], tz)
    assert len(one) == 1 and 1 < len(many) <= service.MAX_CELLS


# ---------- simplification (same behavior as app.trails.service) ----------

def test_dense_lines_are_simplified_but_stay_a_line():
    coords = [[-92.3 + 0.00001 * i, 34.7 + 0.01 * math.sin(i / 50)] for i in range(1000)]
    out = service.simplify_line_coords(coords, service.tolerance_deg(12, 34.7), 34.7)
    assert out and 2 <= len(out) < 1000


def test_degenerate_coordinate_lists_are_not_a_line():
    assert service.simplify_line_coords([[-92.3, 34.7]], 0.0001, 34.7) is None
    assert service.simplify_line_coords([], 0.0001, 34.7) is None


# ---------- normalization ----------

@pytest.mark.parametrize("kind", ["highway", "secondary", "connector", "local"])
def test_features_are_normalized_per_class(kind):
    out = service.normalize_road_feature(road_feature(7, "Main St"), 0.0001, 34.7, kind)
    assert out["id"] == f"{kind}-7"
    assert out["properties"] == {"name": "Main St", "kind": kind, "source": "USGS National Map"}


def test_unnamed_roads_get_a_class_specific_fallback_name():
    out = service.normalize_road_feature(road_feature(8, name=None), 0.0001, 34.7, "local")
    assert out["properties"]["name"] == "Unnamed local road"


def test_features_without_geometry_are_skipped(env):
    env["features"]["local"] = [road_feature(1), {**road_feature(2), "geometry": None}]
    ids = [f["id"] for f in run(**BOX)["features"]]
    assert ids == ["local-1"]


# ---------- the service round trip ----------

def test_request_merges_all_four_road_classes(env):
    env["features"] = {"highway": [road_feature(1)], "secondary": [road_feature(2)],
                        "connector": [road_feature(3)], "local": [road_feature(4)]}
    out = run(**BOX)
    assert out["type"] == "FeatureCollection" and "USGS" in out["attribution"] and out["partial"] is False
    kinds = sorted(f["properties"]["kind"] for f in out["features"])
    assert kinds == ["connector", "highway", "local", "secondary"]
    assert set(env["requests"].keys()) == {"highway", "secondary", "connector", "local"}


def test_cells_are_cached_in_memory_and_in_the_database(env):
    run(**BOX)
    n = len(env["requests"]["local"])
    run(**BOX)
    assert len(env["requests"]["local"]) == n                                   # memory hit
    service._mem.clear()                                                        # a container restart
    out = run(**BOX)
    assert len(env["requests"]["local"]) == n and len(out["features"]) == 1     # database hit


def test_large_areas_are_fetched_in_pages(env, monkeypatch):
    monkeypatch.setattr(service, "PAGE_SIZE", 2)
    env["pages"] = [[road_feature(1), road_feature(2)], [road_feature(3)]]
    out = run(**BOX)
    local_ids = sorted(f["id"] for f in out["features"] if f["properties"]["kind"] == "local")
    assert local_ids == ["local-1", "local-2", "local-3"] and len(env["requests"]["local"]) == 2


def test_old_cells_are_refreshed_but_served_when_usgs_is_down(env):
    run(**BOX)
    for k, (ts, feats) in list(service._mem.items()):
        service._mem[k] = (ts - service.CELL_TTL_S - 60, feats)                 # expire it
    env["status"] = 500
    out = run(**BOX)
    assert len(out["features"]) == 1 and out["partial"] is True                 # stale copy, flagged


def test_nothing_cached_and_usgs_down_is_an_outage(env):
    env["status"] = 503
    with pytest.raises(service.RoadsUnavailable):
        run(**BOX)


def test_concurrent_views_share_one_upstream_fetch(env):
    async def scenario():
        return await asyncio.gather(*[service.roads(**BOX) for _ in range(5)])
    outs = asyncio.run(scenario())
    assert len(env["requests"]["local"]) == 1 and all(len(o["features"]) == 1 for o in outs)


# ---------- cache lifetime ----------

def test_old_cells_are_pruned_and_recent_ones_kept(env):
    now = time.time()
    service._db_store("roads:v1:local:old", now - service.PRUNE_AFTER_S - 100, [])
    service._db_store("roads:v1:local:new", now - 100, [])
    service._mem["roads:v1:local:old"] = (now - service.PRUNE_AFTER_S - 100, [])
    service._mem["roads:v1:local:new"] = (now - 100, [])
    assert service.prune_cache() == 1
    assert service._db_load("roads:v1:local:old") is None and service._db_load("roads:v1:local:new") is not None
    assert "roads:v1:local:old" not in service._mem and "roads:v1:local:new" in service._mem
