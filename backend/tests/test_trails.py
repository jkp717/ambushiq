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
from app.trails import router as trails_router, service
from app.trails.models import TrailCell

BOX = dict(south=34.69, west=-92.31, north=34.71, east=-92.29, zoom=13)        # inside one grid cell
WIDE = dict(south=34.60, west=-92.40, north=34.80, east=-92.20, zoom=13)       # spans several cells


def line(x=-92.30, y=34.70, n=3, dx=0.001):
    return {"type": "LineString", "coordinates": [[x + i * dx, y] for i in range(n)]}


def trail_feature(fid, name="Some Trail", motorcycle="OPEN", atv=None, seasonal=None,
                   forestname="Ouachita National Forest", jurisdiction="FS - FOREST SERVICE", geom=None):
    return {"type": "Feature", "id": fid,
            "properties": {"name": name, "motorcycle": motorcycle, "atv": atv, "seasonal": seasonal,
                            "forestname": forestname, "jurisdiction": jurisdiction},
            "geometry": geom if geom is not None else line()}


def road_feature(fid, name="Some Road", passengervehicle=None, highclearancevehicle="OPEN", truck=None,
                  atv=None, motorcycle=None, forestname="Ouachita National Forest",
                  jurisdiction="FS - FOREST SERVICE", highclearancevehicle_datesopen="Yearlong", geom=None):
    return {"type": "Feature", "id": fid,
            "properties": {"name": name, "passengervehicle": passengervehicle,
                            "highclearancevehicle": highclearancevehicle, "truck": truck, "atv": atv,
                            "motorcycle": motorcycle, "forestname": forestname, "jurisdiction": jurisdiction,
                            "highclearancevehicle_datesopen": highclearancevehicle_datesopen},
            "geometry": geom if geom is not None else line()}


@pytest.fixture()
def env(monkeypatch):
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        s.execute(delete(TrailCell))
        s.commit()
    service._mem.clear()
    service._inflight.clear()
    service._merge_memo.clear()
    monkeypatch.setattr(service.asyncio, "sleep", lambda *_a, **_k: _instant())
    state = {"trail_requests": [], "road_requests": [], "trail_features": [trail_feature(1)],
             "road_features": [road_feature(2)], "trail_status": 200, "road_status": 200, "pages": None}

    def handler(request):
        is_trail = request.url.path.endswith("/2/query")
        state["trail_requests" if is_trail else "road_requests"].append(dict(request.url.params))
        status = state["trail_status"] if is_trail else state["road_status"]
        if status != 200:
            return httpx.Response(status, json={})
        if state["pages"] is not None and is_trail:
            off = int(request.url.params.get("resultOffset", 0)) // int(request.url.params["resultRecordCount"])
            page = state["pages"][off]
            return httpx.Response(200, json={"type": "FeatureCollection", "features": page,
                                             "properties": {"exceededTransferLimit": off < len(state["pages"]) - 1}})
        feats = state["trail_features"] if is_trail else state["road_features"]
        return httpx.Response(200, json={"type": "FeatureCollection", "features": feats})

    real = httpx.AsyncClient
    monkeypatch.setattr(service.httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    return state


async def _instant():
    return None


def run(**box):
    return asyncio.run(service.trails(**box))


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
        asyncio.run(trails_router.trails(south=1, west=1, north=2, east=2, zoom=5, _=None))
    assert bad.value.status_code == 400
    env["trail_status"] = env["road_status"] = 500
    with pytest.raises(HTTPException) as down:
        asyncio.run(trails_router.trails(**BOX, _=None))
    assert down.value.status_code == 502


def test_grid_cells_cover_the_box_without_gaps():
    tz = service.tile_zoom(13)
    assert tz == 12
    one = service.cells_for_bbox(BOX["south"], BOX["west"], BOX["north"], BOX["east"], tz)
    many = service.cells_for_bbox(WIDE["south"], WIDE["west"], WIDE["north"], WIDE["east"], tz)
    assert len(one) == 1 and 1 < len(many) <= service.MAX_CELLS


# ---------- simplification ----------

def test_dense_lines_are_simplified_but_stay_a_line():
    coords = [[-92.3 + 0.00001 * i, 34.7 + 0.01 * math.sin(i / 50)] for i in range(1000)]
    out = service.simplify_line_coords(coords, service.tolerance_deg(12, 34.7), 34.7)
    assert out and 2 <= len(out) < 1000
    assert out[0] == [round(coords[0][0], 5), round(coords[0][1], 5)]
    assert out[-1] == [round(coords[-1][0], 5), round(coords[-1][1], 5)]


def test_degenerate_coordinate_lists_are_not_a_line():
    assert service.simplify_line_coords([[-92.3, 34.7]], 0.0001, 34.7) is None    # a single point isn't a line
    assert service.simplify_line_coords([], 0.0001, 34.7) is None
    # Douglas-Peucker never drops a line's own endpoints, however close together — a real
    # (if tiny) 2-point segment is preserved, not treated as degenerate.
    tiny = [[-92.3, 34.7], [-92.3 + 1e-7, 34.7]]
    out = service.simplify_line_coords(tiny, service.tolerance_deg(12, 34.7), 34.7)
    assert out is not None and len(out) == 2


def test_multilinestring_keeps_survivors_and_collapses_to_linestring_when_only_one_left():
    real = [[-92.3 + 0.0001 * i, 34.7] for i in range(5)]
    degenerate = [[-92.3, 34.7]]                                                  # a single point isn't a line
    geom = {"type": "MultiLineString", "coordinates": [real, degenerate]}
    out = service.simplify_line_geometry(geom, service.tolerance_deg(12, 34.7), 34.7)
    assert out["type"] == "LineString"                                            # only `real` survived
    geom2 = {"type": "MultiLineString", "coordinates": [real, real]}
    out2 = service.simplify_line_geometry(geom2, service.tolerance_deg(12, 34.7), 34.7)
    assert out2["type"] == "MultiLineString" and len(out2["coordinates"]) == 2


# ---------- vehicle-class / seasonal derivation ----------

@pytest.mark.parametrize("motorcycle,atv,expected", [
    ("OPEN", "OPEN", "ATV & motorcycle"),
    (None, "OPEN", "ATV"),
    ("OPEN", None, "Motorcycle"),
    (None, None, "Other OHV"),
    ("CLOSED", None, "Other OHV"),
])
def test_trail_vehicle_class(motorcycle, atv, expected):
    assert service._trail_vehicle_class({"motorcycle": motorcycle, "atv": atv}) == expected


@pytest.mark.parametrize("props,expected", [
    ({"passengervehicle": "OPEN"}, "Passenger vehicle"),
    ({"highclearancevehicle": "OPEN"}, "High-clearance vehicle"),
    ({"truck": "OPEN"}, "Truck"),
    ({"atv": "OPEN"}, "ATV/motorcycle"),
    ({}, "Restricted"),
])
def test_road_vehicle_class(props, expected):
    assert service._road_vehicle_class(props) == expected


def test_road_seasonal_uses_the_field_for_the_derived_vehicle_class():
    props = {"highclearancevehicle": "OPEN", "highclearancevehicle_datesopen": "05/01-12/01", "truck_datesopen": "ignored"}
    assert service._road_seasonal(props, "High-clearance vehicle") == "05/01-12/01"
    assert service._road_seasonal({}, "Restricted") is None


# ---------- normalization ----------

def test_trail_features_are_normalized_with_readable_labels():
    raw = trail_feature(7, "Fourche Mountain Trail", motorcycle="OPEN", atv="OPEN", seasonal="06/16-12/31")
    out = service.normalize_trail_feature(raw, 0.0001, 34.7)
    assert out["id"] == "trail-7"
    assert out["properties"] == {
        "name": "Fourche Mountain Trail", "kind": "trail", "vehicle_class": "ATV & motorcycle",
        "seasonal": "06/16-12/31", "forestname": "Ouachita National Forest",
        "jurisdiction": "FS - FOREST SERVICE", "source": "USFS MVUM",
    }


def test_road_features_are_normalized_with_readable_labels():
    raw = road_feature(9, "FR 1004", highclearancevehicle="OPEN", highclearancevehicle_datesopen="Yearlong")
    out = service.normalize_road_feature(raw, 0.0001, 34.7)
    assert out["id"] == "road-9"
    assert out["properties"]["kind"] == "road"
    assert out["properties"]["vehicle_class"] == "High-clearance vehicle"
    assert out["properties"]["seasonal"] == "Yearlong"


def test_features_without_geometry_are_skipped(env):
    env["trail_features"] = [trail_feature(1), {**trail_feature(2), "geometry": None}]
    ids = [f["id"] for f in run(**BOX)["features"] if f["properties"]["kind"] == "trail"]
    assert ids == ["trail-1"]


# ---------- the service round trip ----------

def test_request_merges_trails_and_roads(env):
    out = run(**BOX)
    assert out["type"] == "FeatureCollection" and "MVUM" in out["attribution"] and out["partial"] is False
    kinds = sorted(f["properties"]["kind"] for f in out["features"])
    assert kinds == ["road", "trail"]
    assert len(env["trail_requests"]) == 1 and len(env["road_requests"]) == 1


def test_cells_are_cached_in_memory_and_in_the_database(env):
    run(**BOX)
    n = len(env["trail_requests"])
    run(**BOX)
    assert len(env["trail_requests"]) == n                                        # memory hit
    service._mem.clear()                                                          # a container restart
    out = run(**BOX)
    assert len(env["trail_requests"]) == n and len(out["features"]) == 2          # database hit


def test_large_areas_are_fetched_in_pages(env, monkeypatch):
    monkeypatch.setattr(service, "PAGE_SIZE", 2)
    env["pages"] = [[trail_feature(1, "A"), trail_feature(2, "B")], [trail_feature(3, "C")]]
    out = run(**BOX)
    trail_ids = sorted(f["id"] for f in out["features"] if f["properties"]["kind"] == "trail")
    assert trail_ids == ["trail-1", "trail-2", "trail-3"] and len(env["trail_requests"]) == 2


def test_old_cells_are_refreshed_but_served_when_usfs_is_down(env):
    run(**BOX)
    for k, (ts, feats) in list(service._mem.items()):
        service._mem[k] = (ts - service.CELL_TTL_S - 60, feats)                   # expire it
    env["trail_status"] = env["road_status"] = 500
    out = run(**BOX)
    assert len(out["features"]) == 2 and out["partial"] is True                   # stale copy, flagged


def test_nothing_cached_and_usfs_down_is_an_outage(env):
    env["trail_status"] = env["road_status"] = 503
    with pytest.raises(service.TrailsUnavailable):
        run(**BOX)


def test_concurrent_views_share_one_upstream_fetch(env):
    async def scenario():
        return await asyncio.gather(*[service.trails(**BOX) for _ in range(5)])
    outs = asyncio.run(scenario())
    assert len(env["trail_requests"]) == 1 and all(len(o["features"]) == 2 for o in outs)


# ---------- cache lifetime ----------

def test_old_cells_are_pruned_and_recent_ones_kept(env):
    now = time.time()
    service._db_store("trails:v1:trail:old", now - service.PRUNE_AFTER_S - 100, [])
    service._db_store("trails:v1:trail:new", now - 100, [])
    service._mem["trails:v1:trail:old"] = (now - service.PRUNE_AFTER_S - 100, [])
    service._mem["trails:v1:trail:new"] = (now - 100, [])
    assert service.prune_cache() == 1
    assert service._db_load("trails:v1:trail:old") is None and service._db_load("trails:v1:trail:new") is not None
    assert "trails:v1:trail:old" not in service._mem and "trails:v1:trail:new" in service._mem


# ---------- merging segments into whole trails/roads ----------

def seg(fid, coords, name="FR 1003", kind="road", vehicle_class="hcv", seasonal=None):
    return {"type": "Feature", "id": f"{kind}-{fid}", "geometry": {"type": "LineString", "coordinates": coords},
            "properties": {"name": name, "kind": kind, "vehicle_class": vehicle_class, "seasonal": seasonal,
                           "forestname": "Ouachita National Forest", "jurisdiction": "FS - FOREST SERVICE",
                           "source": "USFS MVUM"}}


def test_connected_segments_with_matching_details_merge():
    a, b, c = [0, 0], [1, 0], [2, 0]
    out = service.merge_trail_segments([seg(2, [b, c]), seg(1, [a, b])])
    assert [f["id"] for f in out] == ["road-1"]
    assert out[0]["geometry"]["type"] == "MultiLineString" and out[0]["properties"]["name"] == "FR 1003"


def test_segments_with_different_details_stay_separate_so_the_popup_is_accurate():
    a, b, c = [0, 0], [1, 0], [2, 0]
    assert len(service.merge_trail_segments([seg(1, [a, b]), seg(2, [b, c], seasonal="Apr 1 - Dec 15")])) == 2
    assert len(service.merge_trail_segments([seg(1, [a, b]), seg(2, [b, c], vehicle_class="atv")])) == 2
    assert len(service.merge_trail_segments([seg(1, [a, b]), seg(2, [b, c], kind="trail")])) == 2


def test_same_name_but_not_connected_stays_separate():
    out = service.merge_trail_segments([seg(1, [[0, 0], [1, 0]]), seg(2, [[5, 5], [6, 5]])])
    assert sorted(f["id"] for f in out) == ["road-1", "road-2"]


def test_unnamed_trails_join_through_bends_but_stop_at_junctions():
    a, b, c = [0, 0], [1, 0], [2, 0]
    u = dict(name="Unnamed trail", kind="trail")
    assert len(service.merge_trail_segments([seg(1, [a, b], **u), seg(2, [b, c], **u)])) == 1
    assert len(service.merge_trail_segments([seg(1, [a, b], **u), seg(2, [b, c], **u), seg(3, [b, [1, 1]], **u)])) == 3


def test_request_returns_one_feature_per_trail_and_reuses_the_merge(env, monkeypatch):
    env["trail_features"] = [
        trail_feature(1, geom={"type": "LineString", "coordinates": [[-92.30, 34.70], [-92.299, 34.70]]}),
        trail_feature(2, geom={"type": "LineString", "coordinates": [[-92.299, 34.70], [-92.298, 34.70]]}),
    ]
    calls = []
    real = service.merge_trail_segments
    monkeypatch.setattr(service, "merge_trail_segments", lambda f: calls.append(1) or real(f))
    out = run(**BOX)
    trails = [f for f in out["features"] if f["properties"]["kind"] == "trail"]
    assert [f["id"] for f in trails] == ["trail-1"] and trails[0]["geometry"]["type"] == "MultiLineString"
    run(**BOX)
    assert len(calls) == 1
