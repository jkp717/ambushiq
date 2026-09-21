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
from app.publiclands import router as pl_router, service
from app.publiclands.models import PublicLandCell

BOX = dict(south=34.69, west=-92.31, north=34.71, east=-92.29, zoom=13)        # inside one grid cell
TEXAS = dict(south=30.20, west=-97.80, north=30.30, east=-97.70, zoom=13)      # outside Arkansas
WIDE = dict(south=34.60, west=-92.40, north=34.80, east=-92.20, zoom=13)       # spans several cells


def square(x, y, size=0.01):
    return [[[x, y], [x + size, y], [x + size, y + size], [x, y + size], [x, y]]]


def feature(fid, name="Some Land", mang="USFS", mtype="FED", des="NF", access="OA", geom=None):
    return {"type": "Feature", "id": fid,
            "properties": {"Unit_Nm": name, "Mang_Name": mang, "Mang_Type": mtype, "Des_Tp": des, "Pub_Access": access},
            "geometry": geom if geom is not None else {"type": "Polygon", "coordinates": square(-92.30, 34.70)}}


@pytest.fixture()
def env(monkeypatch):
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        s.execute(delete(PublicLandCell))
        s.commit()
    service._mem.clear()
    service._inflight.clear()
    monkeypatch.setattr(service.asyncio, "sleep", lambda *_a, **_k: _instant())
    state = {"requests": [], "features": [feature(1)], "status": 200, "pages": None,
             "agfc": [], "agfc_requests": [], "agfc_status": 200}

    def handler(request):
        if request.url.host == "gis.arkansas.gov":            # the Arkansas Game & Fish WMA layer
            state["agfc_requests"].append(dict(request.url.params))
            if state["agfc_status"] != 200:
                return httpx.Response(state["agfc_status"], json={})
            return httpx.Response(200, json={"type": "FeatureCollection", "features": state["agfc"]})
        state["requests"].append(dict(request.url.params))
        if state["status"] != 200:
            return httpx.Response(state["status"], json={})
        if state["pages"] is not None:
            off = int(request.url.params.get("resultOffset", 0)) // int(request.url.params["resultRecordCount"])
            page = state["pages"][off]
            return httpx.Response(200, json={"type": "FeatureCollection", "features": page,
                                             "properties": {"exceededTransferLimit": off < len(state["pages"]) - 1}})
        return httpx.Response(200, json={"type": "FeatureCollection", "features": state["features"]})

    real = httpx.AsyncClient
    monkeypatch.setattr(service.httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    return state


async def _instant():
    return None


def run(**box):
    return asyncio.run(service.public_lands(**box))


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
        asyncio.run(pl_router.public_lands(south=1, west=1, north=2, east=2, zoom=5, _=None))
    assert bad.value.status_code == 400
    env["status"] = 500
    with pytest.raises(HTTPException) as down:
        asyncio.run(pl_router.public_lands(**BOX, _=None))
    assert down.value.status_code == 502


def test_grid_cells_cover_the_box_without_gaps():
    tz = service.tile_zoom(13)
    assert tz == 12
    one = service.cells_for_bbox(BOX["south"], BOX["west"], BOX["north"], BOX["east"], tz)
    many = service.cells_for_bbox(WIDE["south"], WIDE["west"], WIDE["north"], WIDE["east"], tz)
    assert len(one) == 1 and 1 < len(many) <= service.MAX_CELLS
    size = service.cell_deg(tz)
    xs = sorted({x for x, _ in many})
    assert xs == list(range(xs[0], xs[-1] + 1))                                 # contiguous
    assert (xs[0] * size - 180) <= WIDE["west"] and ((xs[-1] + 1) * size - 180) >= WIDE["east"]


# ---------- simplification ----------

def test_dense_boundaries_are_simplified_and_stay_closed():
    ring = [[-92.3 + 0.01 * math.cos(t / 1000 * 2 * math.pi) + 1e-6 * (t % 3),
             34.7 + 0.01 * math.sin(t / 1000 * 2 * math.pi)] for t in range(1000)]
    ring.append(ring[0])
    out = service.simplify_ring(ring, service.tolerance_deg(12, 34.7), 34.7)
    assert out and len(out) < 200 and out[0] == out[-1]
    assert len(out) >= 4
    xs = [p[0] for p in out]
    assert max(xs) - min(xs) > 0.019                                            # the shape is still about 0.02 degrees wide


def test_slivers_that_vanish_at_this_zoom_are_dropped():
    tiny = {"type": "Polygon", "coordinates": square(-92.3, 34.7, 0.000002)}
    assert service.simplify_geometry(tiny, service.tolerance_deg(12, 34.7), 34.7) is None
    real = {"type": "Polygon", "coordinates": square(-92.3, 34.7, 0.01)}
    assert service.simplify_geometry(real, service.tolerance_deg(12, 34.7), 34.7)["type"] == "Polygon"


def test_a_lost_hole_does_not_lose_the_polygon():
    outer, hole = square(-92.3, 34.7, 0.02)[0], square(-92.295, 34.705, 0.000002)[0]
    out = service.simplify_geometry({"type": "Polygon", "coordinates": [outer, hole]}, service.tolerance_deg(12, 34.7), 34.7)
    assert out and len(out["coordinates"]) == 1


# ---------- classification and normalization ----------

@pytest.mark.parametrize("props,kind", [
    ({"Unit_Nm": "Bayou Meto", "Mang_Name": "SFW", "Mang_Type": "STAT", "Des_Tp": "SCA"}, "wildlife"),
    ({"Unit_Nm": "Dagmar", "Mang_Name": "SFW", "Mang_Type": "STAT", "Des_Tp": "SCA"}, "wildlife"),
    ({"Unit_Nm": "Hull Wildlife Management Area", "Mang_Name": "SDNR", "Mang_Type": "STAT", "Des_Tp": "SCA"}, "wildlife"),
    ({"Unit_Nm": "Ouachita National Forest", "Mang_Name": "USFS", "Mang_Type": "FED", "Des_Tp": "NF"}, "forest"),
    ({"Unit_Nm": "Piney Creek Wilderness", "Mang_Name": "USFS", "Mang_Type": "FED", "Des_Tp": "WA"}, "forest"),
    ({"Unit_Nm": "Holla Bend", "Mang_Name": "FWS", "Mang_Type": "FED", "Des_Tp": "NWR"}, "refuge"),
    ({"Unit_Nm": "Some BLM Tract", "Mang_Name": "BLM", "Mang_Type": "FED", "Des_Tp": "RMA"}, "federal"),
    ({"Unit_Nm": "Pinnacle Mountain State Park", "Mang_Name": "SPR", "Mang_Type": "STAT", "Des_Tp": "SP"}, "state"),
    ({"Unit_Nm": "Glade Preserve", "Mang_Name": "NGO", "Mang_Type": "NGO", "Des_Tp": "PCON"}, "other"),
    ({"Unit_Nm": "Federal Wildlife Area", "Mang_Name": "BLM", "Mang_Type": "FED", "Des_Tp": "RMA"}, "federal"),   # name alone isn't enough off state land
])
def test_land_is_classified_for_styling(props, kind):
    assert service.classify(props) == kind


def test_features_are_normalized_with_readable_labels():
    raw = feature(7, "Bayou Meto", "SFW", "STAT", "SCA", "RA")
    out = service.normalize_feature(raw, service.tolerance_deg(12, 34.7), 34.7)
    assert out["properties"] == {"name": "Bayou Meto", "manager": "State fish & wildlife agency",
                                 "designation": "State Conservation Area", "access": "restricted", "kind": "wildlife",
                                 "source": "USGS PAD-US"}
    unknown = service.normalize_feature(feature(8, None, "ZZZ", "STAT", "QQQ", "??"), 0.0001, 34.7)["properties"]
    assert unknown["access"] == "unknown" and unknown["manager"] == "ZZZ" and unknown["designation"] == "QQQ"
    assert unknown["name"] == "Unnamed public land"


def test_features_without_geometry_are_skipped(env):
    env["features"] = [feature(1), {**feature(2), "geometry": None}]
    assert [f["id"] for f in run(**BOX)["features"]] == [1]


# ---------- the service round trip ----------

def test_request_asks_usgs_only_for_hunting_relevant_land_in_the_cell(env):
    out = run(**BOX)
    assert out["type"] == "FeatureCollection" and "PAD-US" in out["attribution"] and out["partial"] is False
    q = env["requests"][0]
    assert "NOT IN ('LOC','DIST')" in q["where"] and q["f"] == "geojson" and q["inSR"] == "4326"
    west, south, east, north = (float(v) for v in q["geometry"].split(","))
    assert west <= BOX["west"] and east >= BOX["east"] and south <= BOX["south"] and north >= BOX["north"]


def test_features_shared_by_neighbouring_cells_appear_once(env):
    out = run(**WIDE)
    assert len(env["requests"]) > 1 and [f["id"] for f in out["features"]] == [1]


def test_cells_are_cached_in_memory_and_in_the_database(env):
    run(**BOX)
    n = len(env["requests"])
    run(**BOX)
    assert len(env["requests"]) == n                                            # memory hit
    service._mem.clear()                                                        # a container restart
    out = run(**BOX)
    assert len(env["requests"]) == n and len(out["features"]) == 1              # database hit


def test_large_areas_are_fetched_in_pages(env, monkeypatch):
    monkeypatch.setattr(service, "PAGE_SIZE", 2)
    env["pages"] = [[feature(1), feature(2)], [feature(3)]]
    out = run(**BOX)
    assert sorted(f["id"] for f in out["features"]) == [1, 2, 3] and len(env["requests"]) == 2


def test_old_cells_are_refreshed_but_served_when_usgs_is_down(env):
    run(**BOX)
    for k, (ts, feats) in list(service._mem.items()):
        service._mem[k] = (ts - service.CELL_TTL_S - 60, feats)                 # expire it
    env["status"] = 500
    out = run(**BOX)
    assert len(out["features"]) == 1 and out["partial"] is True                 # stale copy, flagged


def test_nothing_cached_and_usgs_down_is_an_outage(env):
    env["status"] = 503
    with pytest.raises(service.PublicLandsUnavailable):
        run(**BOX)


def test_concurrent_views_share_one_upstream_fetch(env):
    async def scenario():
        return await asyncio.gather(*[service.public_lands(**BOX) for _ in range(5)])
    outs = asyncio.run(scenario())
    assert len(env["requests"]) == 1 and all(len(o["features"]) == 1 for o in outs)


# ---------- Arkansas Game & Fish WMA layer ----------

def agfc_feature(fid=81, name="Maumelle River WMA", x=-92.30, y=34.70, size=0.01):
    return {"type": "Feature", "id": fid,
            "properties": {"objectid": fid, "fname": name, "flabel": name, "wma": name},
            "geometry": {"type": "MultiPolygon", "coordinates": [square(x, y, size), square(x + 0.02, y, size)]}}


def test_agfc_is_only_asked_about_cells_in_arkansas(env):
    run(**TEXAS)
    assert env["requests"] and env["agfc_requests"] == []
    run(**BOX)
    assert len(env["agfc_requests"]) >= 1 and env["agfc_requests"][0]["where"] == "1=1"


def test_agfc_wmas_are_added_as_exact_wildlife_areas(env):
    env["agfc"] = [agfc_feature()]
    out = run(**BOX)
    wma = next(f for f in out["features"] if f["properties"]["source"] == "AGFC")
    assert wma["id"] == "agfc-81" and wma["geometry"]["type"] == "MultiPolygon"
    assert wma["properties"] == {"name": "Maumelle River WMA", "manager": "Arkansas Game & Fish Commission",
                                 "designation": "Wildlife Management Area", "access": "restricted",
                                 "kind": "wildlife", "source": "AGFC"}
    assert "Arkansas Game & Fish Commission" in out["attribution"]
    assert "Arkansas Game" not in run(**TEXAS)["attribution"]                  # only credited where it was used


def test_pad_us_wildlife_already_covered_by_agfc_is_not_drawn_twice(env):
    covered = feature(10, "Bayou Meto", "SFW", "STAT", "SCA", "RA",
                      {"type": "Polygon", "coordinates": square(-92.298, 34.702, 0.004)})      # inside the AGFC polygon
    elsewhere = feature(11, "Some Other WMA", "SFW", "STAT", "SCA", "RA",
                        {"type": "Polygon", "coordinates": square(-92.28, 34.72, 0.004)})      # AGFC doesn't have it
    forest = feature(12, "Ouachita National Forest", "USFS", "FED", "NF", "OA",
                     {"type": "Polygon", "coordinates": square(-92.298, 34.702, 0.004)})       # overlaps, but isn't a WMA
    env["features"] = [covered, elsewhere, forest]
    env["agfc"] = [agfc_feature()]
    ids = {f["id"] for f in run(**BOX)["features"]}
    assert ids == {11, 12, "agfc-81"}                                            # 10 was the duplicate


def test_without_agfc_data_pad_us_wildlife_is_kept(env):
    env["features"] = [feature(10, "Bayou Meto", "SFW", "STAT", "SCA", "RA")]
    assert [f["id"] for f in run(**BOX)["features"]] == [10]


def test_agfc_outage_leaves_pad_us_intact_and_flags_partial(env):
    env["agfc_status"] = 503
    env["features"] = [feature(10, "Bayou Meto", "SFW", "STAT", "SCA", "RA")]
    out = run(**BOX)
    assert [f["id"] for f in out["features"]] == [10] and out["partial"] is True


def test_pad_us_outage_still_shows_the_agfc_wmas(env):
    env["status"] = 503
    env["agfc"] = [agfc_feature()]
    out = run(**BOX)
    assert [f["id"] for f in out["features"]] == ["agfc-81"] and out["partial"] is True


def test_point_in_polygon_respects_holes():
    outer, hole = square(0, 0, 10)[0], square(4, 4, 2)[0]
    rings = [outer, hole]
    assert service._in_polygon(1, 1, rings) and not service._in_polygon(5, 5, rings) and not service._in_polygon(11, 5, rings)


# ---------- cache lifetime ----------

def test_empty_cells_are_rechecked_after_a_day_but_full_ones_are_not(env):
    env["features"] = []
    run(**TEXAS)
    n = len(env["requests"])
    for k, (ts, feats) in list(service._mem.items()):
        service._mem[k] = (ts - service.EMPTY_CELL_TTL_S - 60, feats)
    run(**TEXAS)
    assert len(env["requests"]) == 2 * n                                         # empty cells: every one fetched again

    env["features"] = [feature(1)]
    run(**BOX)
    m = len(env["requests"])
    for k, (ts, feats) in list(service._mem.items()):
        service._mem[k] = (ts - 2 * service.EMPTY_CELL_TTL_S, feats)
    run(**BOX)
    assert len(env["requests"]) == m                                             # non-empty: still fresh (30 days)


def test_old_cells_are_pruned_and_recent_ones_kept(env):
    now = time.time()
    service._db_store("plands:v1:old", now - service.PRUNE_AFTER_S - 100, [])
    service._db_store("plands:v1:new", now - 100, [])
    service._mem["plands:v1:old"] = (now - service.PRUNE_AFTER_S - 100, [])
    service._mem["plands:v1:new"] = (now - 100, [])
    assert service.prune_cache() == 1
    assert service._db_load("plands:v1:old") is None and service._db_load("plands:v1:new") is not None
    assert "plands:v1:old" not in service._mem and "plands:v1:new" in service._mem


def test_a_duplicate_with_the_same_boundary_is_still_recognised(env):
    same = feature(10, "Bayou Meto", "SFW", "STAT", "SCA", "RA",
                   {"type": "Polygon", "coordinates": square(-92.30, 34.70, 0.01)})       # outline exactly on the AGFC edge
    partly = feature(11, "Half Covered", "SFW", "STAT", "SCA", "RA",
                     {"type": "Polygon", "coordinates": square(-92.305, 34.70, 0.01)})     # ~50% overlapped
    barely = feature(12, "Mostly Elsewhere", "SFW", "STAT", "SCA", "RA",
                     {"type": "Polygon", "coordinates": square(-92.295, 34.70, 0.02)})     # AGFC covers ~1/3 of it
    env["features"] = [same, partly, barely]
    env["agfc"] = [{**agfc_feature(), "geometry": {"type": "Polygon", "coordinates": square(-92.30, 34.70, 0.01)}}]
    ids = {f["id"] for f in run(**BOX)["features"]}
    assert 10 not in ids and 12 in ids
