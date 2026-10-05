import asyncio
import math

import pytest
from sqlalchemy.orm import Session

import app.main  # noqa: F401  (imports every router/model so create_all sees all tables)
from app.core.database import Base, engine
from app.forecast.schemas import HourRankIn
from app.jobs import service as jobs
from app.regions import terrain as rt
from app.regions.models import Region
from app.stands.models import Stand

LAT0, LON0 = 34.7, -92.3
M_PER_DEG = 111320.0


@pytest.fixture()
def db(monkeypatch):
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    jobs._running.clear()
    rt._grid_cache.clear()
    with Session(engine) as s:
        s.add(Region(id=1, name="R", lat=LAT0, lon=LON0, rut_peak_month=12, rut_peak_day=5,
                     property_timezone="America/Chicago", is_default=1, created_at=""))
        s.add(Stand(region_id=1, name="A", lat=LAT0, lon=LON0))
        s.add(Stand(region_id=1, name="B", lat=LAT0 + 0.005, lon=LON0 + 0.005))   # ~0.5 km apart
        s.commit()

    calls = []

    async def fake_usgs(client, lats, lons, cb=None, progress_span=(0, 75)):
        calls.append(len(lats))
        # a gentle slope so the grid isn't flat; values are irrelevant to these tests
        return [100.0 + r for r in range(len(lats)) for _ in range(len(lons))]

    monkeypatch.setattr(rt, "_fetch_usgs", fake_usgs)
    return calls


def _add_stand(name, lat, lon):
    with Session(engine) as s:
        s.add(Stand(region_id=1, name=name, lat=lat, lon=lon))
        s.commit()


def _fetch():
    asyncio.run(jobs.run_tracked("property_terrain", "manual", region_id=1))
    return jobs.job_list(1)[0]["last_run"]


def test_small_property_gets_the_minimum_box_around_its_stands(db):
    clat, clon, side = rt.property_box(1)
    assert side == rt.MIN_BOX_M
    assert abs(clat - (LAT0 + 0.0025)) < 1e-9 and abs(clon - (LON0 + 0.0025)) < 1e-9


def test_spread_out_stands_widen_the_box_up_to_the_cap(db):
    _add_stand("C", LAT0 + 0.025, LON0)                 # ~2.8 km north of A
    assert rt.property_box(1)[2] == pytest.approx(0.025 * M_PER_DEG + 2 * rt.MARGIN_M, rel=1e-6)
    _add_stand("Far", LAT0 + 0.2, LON0)                 # ~22 km away
    assert rt.property_box(1)[2] == rt.MAX_BOX_M


def test_job_stores_a_grid_that_covers_every_stand(db):
    run = _fetch()
    assert run["status"] == "success" and "covers 2 of 2 stands" in run["message"]
    assert run["message"].startswith("3.1 mi box · 98 ft cells")
    grid = rt.load_grid(1)
    assert grid.dem.shape == (168, 168) and grid.cell_m == pytest.approx(5000 / 167)
    assert rt.covers(grid, LAT0, LON0)
    assert not rt.needs_refresh(1)


def test_missing_grid_needs_a_fetch(db):
    assert rt.needs_refresh(1)


def test_a_stand_outside_the_grid_triggers_a_bigger_fetch(db):
    _fetch()
    _add_stand("C", LAT0 + 0.03, LON0)                  # ~3.3 km north: outside the 5 km box's margin
    assert rt.needs_refresh(1)
    run = _fetch()
    assert "covers 3 of 3 stands" in run["message"]
    assert rt.covers(rt.load_grid(1), LAT0 + 0.03, LON0)
    assert not rt.needs_refresh(1)


def test_a_stand_too_far_to_fit_does_not_keep_retriggering(db):
    _add_stand("Far", LAT0 + 0.2, LON0)
    run = _fetch()
    assert "too far out" in run["message"] and "Far" in run["message"]
    assert not rt.needs_refresh(1)


def test_open_meteo_fallback_uses_coarser_cells(db, monkeypatch):
    async def broken(*a, **k):
        raise ValueError("usgs down")

    async def fake_om(client, lats, lons):
        return [100.0] * (len(lats) * len(lons))

    monkeypatch.setattr(rt, "_fetch_usgs", broken)
    monkeypatch.setattr(rt, "_fetch_open_meteo", fake_om)
    run = _fetch()
    assert run["status"] == "success"
    assert rt.load_grid(1).cell_m == pytest.approx(5000 / 56)


def test_ensure_starts_the_job_only_when_needed(db, monkeypatch):
    started = []
    monkeypatch.setattr(jobs, "start_background", lambda job_id, region_id=None: started.append((job_id, region_id)))
    rt.ensure_property_terrain(1)
    assert started == [("property_terrain", 1)]
    _fetch()
    rt.ensure_property_terrain(1)
    assert len(started) == 1


def test_ensure_swallows_a_busy_job(db, monkeypatch):
    def busy(*a, **k):
        raise jobs.JobBusy("property_terrain")
    monkeypatch.setattr(jobs, "start_background", busy)
    rt.ensure_property_terrain(1)   # no exception


def test_map_conditions_returns_one_lee_zone_layer(db, monkeypatch):
    # a steep NW ridge over the property so a NW wind makes an eddy
    async def ridge(client, lats, lons, cb=None, progress_span=(0, 75)):
        clat, clon = (lats[0] + lats[-1]) / 2, (lons[0] + lons[-1]) / 2
        out = []
        for la in lats:
            for lo in lons:
                s = ((lo - clon) * M_PER_DEG * math.cos(math.radians(clat)) * -0.7071
                     + (la - clat) * M_PER_DEG * 0.7071)
                out.append(100.0 + min(250.0, max(0.0, s * 0.45)))
        return out
    monkeypatch.setattr(rt, "_fetch_usgs", ridge)
    _fetch()

    from app.forecast import router as fc_router
    from test_endpoints_smoke import _forecast

    async def windy_forecast(*a, **k):
        fc = _forecast()
        n = len(fc["hourly"]["time"])
        fc["hourly"]["wind_direction_10m"] = [315.0] * n
        fc["hourly"]["wind_speed_10m"] = [14.0] * n
        fc["hourly"]["wind_gusts_10m"] = [22.0] * n
        return fc

    monkeypatch.setattr(fc_router, "get_forecast", windy_forecast)
    monkeypatch.setattr(fc_router.detection_mod, "species_available", lambda: False)
    monkeypatch.setattr(fc_router, "ensure_property_terrain", lambda region_id: None)

    on = asyncio.run(fc_router.map_conditions(HourRankIn(time_index=12, lee_zone=True), region_id=1))
    off = asyncio.run(fc_router.map_conditions(HourRankIn(time_index=12), region_id=1))
    assert off["lee_zone"] is None
    assert len(on["lee_zone"]["rows"]) == 168 and "1" in "".join(on["lee_zone"]["rows"])
    assert all("lee_zone" not in it["vectors"] for it in on["stands"])
    assert any(it["vectors"]["lee_eddy"] for it in on["stands"])
