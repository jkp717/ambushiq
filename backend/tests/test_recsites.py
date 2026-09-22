import asyncio
import time

import httpx
import pytest
from fastapi import HTTPException
from sqlalchemy import delete
from sqlalchemy.orm import Session

import app.main  # noqa: F401  (registers every model so create_all sees all tables)
from app.core.database import Base, engine
from app.recsites import router as recsites_router, service
from app.recsites.models import RecSiteCell

BOX = dict(south=34.69, west=-92.31, north=34.71, east=-92.29, zoom=13)        # inside one grid cell
WIDE = dict(south=34.60, west=-92.40, north=34.80, east=-92.20, zoom=13)       # spans several cells


def point(x=-92.30, y=34.70):
    return {"type": "Point", "coordinates": [x, y]}


def site(fid, name="Some Site", subtype="TRAILHEAD", recarea_name="Ouachita National Forest",
         fee_charged=None, fee_description=None, aba_accessible=None, geom=None):
    return {"type": "Feature", "id": fid,
            "properties": {"site_name": name, "site_subtype": subtype, "recarea_name": recarea_name,
                            "directions": None, "fee_charged": fee_charged, "fee_description": fee_description,
                            "open_season": None, "best_season": None, "restrictions": None,
                            "aba_accessible": aba_accessible, "max_nbr_vehicles": None,
                            "water_availability": None, "restroom_availability": None},
            "geometry": geom if geom is not None else point()}


@pytest.fixture()
def env(monkeypatch):
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        s.execute(delete(RecSiteCell))
        s.commit()
    service._mem.clear()
    service._inflight.clear()
    monkeypatch.setattr(service.asyncio, "sleep", lambda *_a, **_k: _instant())
    state = {"requests": [], "features": [site(1)], "status": 200, "pages": None}

    def handler(request):
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
    return asyncio.run(service.recreation_sites(**box))


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
        asyncio.run(recsites_router.recreation_sites(south=1, west=1, north=2, east=2, zoom=5, _=None))
    assert bad.value.status_code == 400
    env["status"] = 500
    with pytest.raises(HTTPException) as down:
        asyncio.run(recsites_router.recreation_sites(**BOX, _=None))
    assert down.value.status_code == 502


def test_grid_cells_cover_the_box_without_gaps():
    tz = service.tile_zoom(13)
    assert tz == 12
    one = service.cells_for_bbox(BOX["south"], BOX["west"], BOX["north"], BOX["east"], tz)
    many = service.cells_for_bbox(WIDE["south"], WIDE["west"], WIDE["north"], WIDE["east"], tz)
    assert len(one) == 1 and 1 < len(many) <= service.MAX_CELLS


# ---------- normalization ----------

@pytest.mark.parametrize("subtype,kind", [
    ("TRAILHEAD", "trailhead"), ("CAMPGROUND", "campground"),
    ("PICNIC SITE", "picnic"), ("DAY USE AREA", "day_use"),
])
def test_recognised_subtypes_are_mapped_to_kinds(subtype, kind):
    out = service.normalize_feature(site(1, subtype=subtype))
    assert out["properties"]["kind"] == kind


def test_unrecognised_subtypes_are_dropped():
    assert service.normalize_feature(site(1, subtype="BOATING SITE")) is None


def test_features_without_point_geometry_are_dropped():
    assert service.normalize_feature({**site(1), "geometry": None}) is None
    assert service.normalize_feature({**site(1), "geometry": {"type": "Polygon", "coordinates": []}}) is None


def test_fee_falls_back_to_a_flag_when_no_description_is_given():
    described = service.normalize_feature(site(1, fee_charged="Y", fee_description="Day-use fee: $5"))
    assert described["properties"]["fee"] == "Day-use fee: $5"
    flagged = service.normalize_feature(site(2, fee_charged="Y", fee_description=None))
    assert flagged["properties"]["fee"] == "Fee charged"
    free = service.normalize_feature(site(3, fee_charged=None, fee_description=None))
    assert free["properties"]["fee"] is None


def test_features_are_normalized_with_readable_properties():
    out = service.normalize_feature(site(7, "Buffalo Gap Trailhead", "TRAILHEAD", "Ouachita National Forest",
                                          aba_accessible="Y"))
    assert out["id"] == "rec-7"
    assert out["properties"]["name"] == "Buffalo Gap Trailhead"
    assert out["properties"]["kind"] == "trailhead"
    assert out["properties"]["recarea"] == "Ouachita National Forest"
    assert out["properties"]["accessible"] is True
    assert out["properties"]["source"] == "USFS EDW"


# ---------- the service round trip ----------

def test_request_filters_to_the_four_requested_subtypes(env):
    out = run(**BOX)
    assert out["type"] == "FeatureCollection" and out["partial"] is False
    q = env["requests"][0]
    assert q["where"] == "site_subtype IN ('TRAILHEAD','CAMPGROUND','PICNIC SITE','DAY USE AREA')"
    assert q["f"] == "geojson" and q["outSR"] == "4326"


def test_features_shared_by_neighbouring_cells_appear_once(env):
    out = run(**WIDE)
    assert len(env["requests"]) > 1 and [f["id"] for f in out["features"]] == ["rec-1"]


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
    env["pages"] = [[site(1), site(2)], [site(3)]]
    out = run(**BOX)
    assert sorted(f["id"] for f in out["features"]) == ["rec-1", "rec-2", "rec-3"] and len(env["requests"]) == 2


def test_old_cells_are_refreshed_but_served_when_usfs_is_down(env):
    run(**BOX)
    for k, (ts, feats) in list(service._mem.items()):
        service._mem[k] = (ts - service.CELL_TTL_S - 60, feats)                 # expire it
    env["status"] = 500
    out = run(**BOX)
    assert len(out["features"]) == 1 and out["partial"] is True                 # stale copy, flagged


def test_nothing_cached_and_usfs_down_is_an_outage(env):
    env["status"] = 503
    with pytest.raises(service.RecSitesUnavailable):
        run(**BOX)


def test_concurrent_views_share_one_upstream_fetch(env):
    async def scenario():
        return await asyncio.gather(*[service.recreation_sites(**BOX) for _ in range(5)])
    outs = asyncio.run(scenario())
    assert len(env["requests"]) == 1 and all(len(o["features"]) == 1 for o in outs)


# ---------- cache lifetime ----------

def test_old_cells_are_pruned_and_recent_ones_kept(env):
    now = time.time()
    service._db_store("recsites:v1:old", now - service.PRUNE_AFTER_S - 100, [])
    service._db_store("recsites:v1:new", now - 100, [])
    service._mem["recsites:v1:old"] = (now - service.PRUNE_AFTER_S - 100, [])
    service._mem["recsites:v1:new"] = (now - 100, [])
    assert service.prune_cache() == 1
    assert service._db_load("recsites:v1:old") is None and service._db_load("recsites:v1:new") is not None
    assert "recsites:v1:old" not in service._mem and "recsites:v1:new" in service._mem
