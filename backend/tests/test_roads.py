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
    service._merge_memo.clear()
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
    assert out["properties"] == {"name": "Main St", "kind": kind, "named": True, "route": None,
                                 "source": "USGS National Map"}


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
    env["pages"] = [[road_feature(1, "A Rd"), road_feature(2, "B Rd")], [road_feature(3, "C Rd")]]
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


# ---------- merging segments into whole roads ----------

def seg(fid, coords, name="Oak Rd", kind="local"):
    return {"type": "Feature", "id": f"{kind}-{fid}", "geometry": {"type": "LineString", "coordinates": coords},
            "properties": {"name": name, "kind": kind, "source": "USGS National Map"}}


def by_id(out):
    return {f["id"]: f for f in out}


def test_connected_segments_of_one_road_merge_into_one_feature():
    a, b, c = [0, 0], [1, 0], [2, 0]
    out = service.merge_road_segments([seg(2, [b, c]), seg(1, [a, b]), seg(3, [b, [1, 1]], name="Elm St")])
    f = by_id(out)
    assert set(f) == {"local-1", "local-3"}
    assert f["local-1"]["geometry"]["type"] == "MultiLineString" and len(f["local-1"]["geometry"]["coordinates"]) == 2
    assert f["local-1"]["properties"] == {"name": "Oak Rd", "kind": "local", "source": "USGS National Map"}
    assert f["local-3"]["geometry"]["type"] == "LineString"                     # a lone segment is untouched


def test_same_name_but_not_connected_stays_separate():
    out = service.merge_road_segments([seg(1, [[0, 0], [1, 0]]), seg(2, [[5, 5], [6, 5]])])
    assert sorted(f["id"] for f in out) == ["local-1", "local-2"]


def test_different_kinds_with_the_same_name_do_not_merge():
    out = service.merge_road_segments([seg(1, [[0, 0], [1, 0]]), seg(1, [[1, 0], [2, 0]], kind="secondary")])
    assert len(out) == 2


def test_named_road_chains_through_many_splits_and_junctions():
    pts = [[i, 0] for i in range(6)]
    segs = [seg(i, [pts[i], pts[i + 1]]) for i in range(5)] + [seg(9, [pts[2], [2, 1]], name="Side St")]
    out = service.merge_road_segments(segs)
    assert len(out) == 2 and len(by_id(out)["local-0"]["geometry"]["coordinates"]) == 5


def test_unnamed_segments_join_through_bends_but_stop_at_junctions():
    u = service._UNNAMED["local"]
    a, b, c, d = [0, 0], [1, 0], [2, 0], [3, 0]
    bend = service.merge_road_segments([seg(1, [a, b], name=u), seg(2, [b, c], name=u)])
    assert len(bend) == 1
    junction = service.merge_road_segments([seg(1, [a, b], name=u), seg(2, [b, c], name=u), seg(3, [b, [1, 1]], name=u)])
    assert len(junction) == 3
    crossed = service.merge_road_segments([seg(1, [b, c], name=u), seg(2, [c, d], name=u), seg(3, [c, [2, 1]], name="Elm St")])
    assert len(crossed) == 3                                                    # a named road crossing is a junction too


def test_merged_ids_do_not_depend_on_input_order():
    s1, s2 = seg(7, [[0, 0], [1, 0]]), seg(4, [[1, 0], [2, 0]])
    assert [f["id"] for f in service.merge_road_segments([s1, s2])] == \
           [f["id"] for f in service.merge_road_segments([s2, s1])] == ["local-4"]


def test_request_returns_one_feature_per_road(env):
    env["features"]["local"] = [
        road_feature(1, geom={"type": "LineString", "coordinates": [[-92.30, 34.70], [-92.299, 34.70]]}),
        road_feature(2, geom={"type": "LineString", "coordinates": [[-92.299, 34.70], [-92.298, 34.70]]}),
    ]
    out = run(**BOX)
    assert [f["id"] for f in out["features"]] == ["local-1"]
    assert out["features"][0]["geometry"]["type"] == "MultiLineString"


def test_merge_is_reused_for_the_same_cells_and_redone_after_a_refresh(env, monkeypatch):
    calls = []
    real = service.merge_road_segments
    monkeypatch.setattr(service, "merge_road_segments", lambda f: calls.append(1) or real(f))
    first = run(**BOX)
    second = run(**BOX)
    assert len(calls) == 1 and second["features"] is first["features"]          # panning in the same cells
    service._mem.clear()                                                        # cells reloaded (new lists)
    run(**BOX)
    assert len(calls) == 2


# ---------- names: USGS name, else Forest Service route number ----------

def test_forest_service_route_number_names_a_road_usgs_leaves_unnamed():
    raw = {"id": 5, "properties": {"name": "", "federal_lands_route": " 423I "}, "geometry": line()}
    p = service.normalize_road_feature(raw, 0.00001, 34.7, "local")["properties"]
    assert p["name"] == "FS Rd 423I" and p["route"] == "423I" and p["named"] is False


def test_usgs_name_wins_and_is_marked_named():
    raw = {"id": 5, "properties": {"name": "Scott Ln", "federal_lands_route": "423I"}, "geometry": line()}
    p = service.normalize_road_feature(raw, 0.00001, 34.7, "local")["properties"]
    assert p["name"] == "Scott Ln" and p["named"] is True and p["route"] == "423I"


def test_no_name_and_no_route_keeps_the_placeholder():
    raw = {"id": 5, "properties": {"name": None}, "geometry": line()}
    p = service.normalize_road_feature(raw, 0.00001, 34.7, "local")["properties"]
    assert p["name"] == service._UNNAMED["local"] and p["named"] is False and p["route"] is None


def test_route_named_segments_merge_through_junctions():
    a, b, c = [0, 0], [1, 0], [2, 0]
    u = service._UNNAMED["local"]
    out = service.merge_road_segments([seg(1, [a, b], name="FS Rd 423I"), seg(2, [b, c], name="FS Rd 423I"),
                                       seg(3, [b, [1, 1]], name=u)])
    assert len(out) == 2


# ---------- under_fs: USGS roads left out where a Forest Service road draws them ----------

def test_cut_under_fs_removes_covered_stretches_and_keeps_the_rest():
    fs = [{"type": "Feature", "id": "road-1", "properties": {"kind": "road"},
           "geometry": {"type": "LineString", "coordinates": [[-92.300, 34.70], [-92.290, 34.70]]}}]
    along = seg(1, [[-92.300, 34.7001], [-92.290, 34.7001]])
    past = seg(2, [[-92.295, 34.7001], [-92.280, 34.7001]])
    away = seg(3, [[-92.300, 34.75], [-92.290, 34.75]])
    out = by_id(service.cut_under_fs([along, past, away], fs))
    assert set(out) == {"local-2~0", "local-3"}
    assert out["local-2~0"]["geometry"]["coordinates"][-1] == [-92.28, 34.7001]
    assert out["local-3"] is away


def test_request_with_under_fs_leaves_out_roads_under_fs_roads(env, monkeypatch):
    env["features"]["local"] = [road_feature(1, geom={"type": "LineString",
                                                      "coordinates": [[-92.300, 34.7001], [-92.292, 34.7001]]})]
    fs_cell = [{"type": "Feature", "id": "road-9", "properties": {"kind": "road"},
                "geometry": {"type": "LineString", "coordinates": [[-92.301, 34.70], [-92.291, 34.70]]}}]

    async def fs_cells(_tz, _cells):
        return [fs_cell]
    monkeypatch.setattr(service, "_fs_road_cells", fs_cells)
    assert run(**BOX, under_fs=True)["features"] == []
    assert [f["id"] for f in run(**BOX)["features"]] == ["local-1"]             # flag off: drawn as usual


def test_fs_outage_hides_nothing(env, monkeypatch):
    async def none(_tz, _cells):
        return []
    monkeypatch.setattr(service, "_fs_road_cells", none)
    assert [f["id"] for f in run(**BOX, under_fs=True)["features"]] == ["local-1"]


# ---------- feature ids: two segments from one junction must not collide ----------

def test_objectid_is_requested_so_usgs_sends_feature_ids(env):
    run(**BOX)
    assert "objectid" in env["requests"]["local"][0]["outFields"].split(",")


def test_fallback_ids_differ_for_segments_sharing_a_starting_junction():
    a = {"properties": {"name": "10"}, "geometry": {"type": "LineString", "coordinates": [[0, 0], [1, 0]]}}
    b = {"properties": {"name": "10"}, "geometry": {"type": "LineString", "coordinates": [[0, 0], [0, 1]]}}
    ids = {service.normalize_road_feature(f, 0.00001, 0, "local")["id"] for f in (a, b)}
    assert len(ids) == 2
