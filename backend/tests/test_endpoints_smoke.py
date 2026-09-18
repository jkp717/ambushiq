"""End-to-end wiring check for the ranking endpoints: real routers and scoring code, an
in-memory SQLite database, and a synthetic forecast (no network)."""
import asyncio
import json

import numpy as np
import pytest

import app.main  # noqa: F401  (imports every router/model so create_all sees all tables)
from app.core.database import Base, engine
from app.deer_ratings import router as deer_router
from app.forecast import router as fc_router
from app.forecast.schemas import DayRankIn, HourRankIn, ManualRankIn, SitRankIn
from app.regions.models import Region
from app.stands import terrain as terrain_mod
from app.stands.models import Stand
from app.zones.models import Zone
from app.corridors.models import Corridor
from app.deer_sign.models import DeerSign
from sqlalchemy.orm import Session

DAYS = ["2025-11-10", "2025-11-11"]


def _forecast():
    times = [f"{d}T{h:02d}:00" for d in DAYS for h in range(24)]
    n = len(times)
    hourly = {
        "time": times,
        "wind_direction_10m": [200.0 + (i % 24) for i in range(n)],
        "wind_speed_10m": [3.0 + (i % 12) * 0.8 for i in range(n)],
        "wind_gusts_10m": [6.0 + (i % 12) for i in range(n)],
        "shortwave_radiation": [max(0.0, 500 * np.sin(np.pi * ((i % 24) - 6.5) / 11)) if 7 <= i % 24 <= 17 else 0.0
                                for i in range(n)],
        "temperature_2m": [4 + 8 * np.sin(np.pi * ((i % 24) - 8) / 12) for i in range(n)],
        "cloud_cover": [30] * n,
        "surface_pressure": [None] * n,
        "pressure_msl": [1016.0 - 0.05 * i for i in range(n)],
        "precipitation": [0.0] * n,
        "dew_point_2m": [2.0] * n,
    }
    daily = {"sunrise": [f"{d}T06:45" for d in DAYS], "sunset": [f"{d}T17:05" for d in DAYS]}
    return {"hourly": hourly, "daily": daily, "utc_offset_seconds": -6 * 3600, "elevation": 120.0}


@pytest.fixture()
def seeded(monkeypatch):
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    cols = np.arange(41, dtype=np.float32)
    dem = np.tile(300.0 + 0.15 * 20.0 * cols, (41, 1)) + np.arange(41, dtype=np.float32)[:, None] * 0.5
    terrain = terrain_mod.analyze_terrain(dem.tolist(), 20.0, "test")
    with Session(engine) as s:
        s.add(Region(id=1, name="R", lat=34.7, lon=-92.3, rut_peak_month=12, rut_peak_day=5,
                     property_timezone="America/Chicago", is_default=1, created_at=""))
        s.add(Stand(region_id=1, name="Ridge", lat=34.7, lon=-92.3, downhill_deg=terrain["downhill_deg"],
                    deer_approach_deg=270, terrain_json=json.dumps(terrain)))
        s.add(Stand(region_id=1, name="Bare", lat=34.701, lon=-92.301))   # never analysed
        s.add(Zone(region_id=1, kind="food", name="plot", lat=34.7005, lon=-92.3, radius_m=40, quality=8))
        s.add(Corridor(region_id=1, name="trail", points_json=json.dumps([[34.699, -92.301], [34.702, -92.299]])))
        s.add(DeerSign(region_id=1, kind="scrape", name="s", lat=34.7002, lon=-92.3001, created_at=""))
        s.commit()

    async def fake_forecast(*a, **k):
        return _forecast()

    async def none(*a, **k):
        return None

    monkeypatch.setattr(fc_router, "get_forecast", fake_forecast)
    monkeypatch.setattr(deer_router, "get_forecast", fake_forecast)
    monkeypatch.setattr(deer_router, "get_historical_highs_f", none)
    monkeypatch.setattr(deer_router, "get_historical_day_weather", none)
    monkeypatch.setattr(fc_router.detection_mod, "species_available", lambda: False)


def run(coro):
    return asyncio.run(coro)


def test_map_and_day_views_agree_for_the_same_stand_and_hour(seeded):
    day = run(fc_router.day_ranked(DayRankIn(day=DAYS[0]), region_id=1, _=None))
    assert {r["stand"]["name"] for r in day["ranked"]} == {"Ridge", "Bare"}
    checked = 0
    for row in day["ranked"]:
        for period, best in row["periods"].items():
            if not best:
                continue
            hour = best["hour"]
            idx = next(i for i, t in enumerate(_forecast()["hourly"]["time"])
                       if t == f"{hour['date']}T{hour['time_h']:02d}:00")
            mp = run(fc_router.map_conditions(HourRankIn(time_index=idx), region_id=1, _=None))
            same = next(r for r in mp["ranked"] if r["stand"]["id"] == row["stand"]["id"])
            assert same["avg"] == pytest.approx(best["score"]["total"], abs=1e-6), (row["stand"]["name"], period)
            checked += 1
    assert checked >= 4


def test_unanalysed_stand_has_no_thermal_arrow_but_analysed_one_does(seeded):
    mp = run(fc_router.map_conditions(HourRankIn(time_index=7), region_id=1, _=None))   # 7 AM, sinking
    vecs = {it["stand"]["name"]: it["vectors"] for it in mp["stands"]}
    assert vecs["Bare"]["thermal_to_deg"] is None and vecs["Bare"]["thermal_strength"] == 0.0
    assert vecs["Ridge"]["thermal_to_deg"] is not None and 0 < vecs["Ridge"]["thermal_strength"] <= 1
    assert vecs["Ridge"]["wind_speed"] > 0 and "scent_to_deg" in vecs["Ridge"]


def test_day_ranking_includes_bounded_proximity_and_period_winners(seeded):
    day = run(fc_router.day_ranked(DayRankIn(day=DAYS[1]), region_id=1, _=None))
    ridge = next(r for r in day["ranked"] if r["stand"]["name"] == "Ridge")
    assert 0 < ridge["proximity"]["total"] <= 1.5 * (0.15 + 0.15 + 0.10 + 0.12 + 0.10)
    assert set(day["winners"]) == {"morning", "midday", "evening"}
    flat = day["ranked"][0]["periods"]["morning"]["score"]
    assert {"total", "breakdown", "camera", "scent_to_deg", "thermal_phase", "drainage_deg"} <= set(flat)


def test_toggles_zero_out_proximity_types(seeded):
    on = run(fc_router.day_ranked(DayRankIn(day=DAYS[0]), region_id=1, _=None))
    off = run(fc_router.day_ranked(DayRankIn(day=DAYS[0], use_food=False, use_corridor=False), region_id=1, _=None))
    ridge = lambda d: next(r for r in d["ranked"] if r["stand"]["name"] == "Ridge")["proximity"]
    assert ridge(off)["food"] == 0 and ridge(off)["corridor"] == 0 and ridge(on)["food"] > 0


def test_sit_and_manual_rankings_run_through_the_same_pipeline(seeded):
    sit = run(fc_router.rank_sit(SitRankIn(sit_idxs=[6, 7, 8, 9], sunrise_h=6.75, sunset_h=17.08), region_id=1, _=None))
    assert len(sit["ranked"]) == 2 and sit["ranked"][0]["avg"] >= sit["ranked"][1]["avg"]
    assert "breakdown" in sit["ranked"][0]["sample"]["score"]
    manual = fc_router.rank_manual(ManualRankIn(wind_dir="SW", wind_speed=5, gust=8, period="morning"),
                                   region_id=1, _=None)
    assert len(manual["ranked"]) == 2 and manual["ranked"][0]["avg"] > 0


def test_deer_ratings_endpoint_uses_sea_level_pressure_and_full_scale(seeded):
    out = run(deer_router.deer_ratings(region_id=1, _=None))
    assert [r["day"] for r in out["ratings"]] == DAYS
    for r in out["ratings"]:
        assert 1 <= r["rating"] <= 5
        assert 29.9 < r["inputs"]["pressure_inhg"] < 30.2      # 1016 hPa MSL ≈ 30.0 inHg
    assert out["previous_day"] is None
