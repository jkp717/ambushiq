"""Hunt conditions per sit: swirling winds (wind swings or a lee eddy), heavy rain, high wind."""
import math

import numpy as np

from app.forecast import eddy, hunt

N, CELL = 81, 50.0
SETTINGS = {"hunt_rain_mm": 2.5, "hunt_wind_mph": 18.0, "hunt_gust_mph": 30.0}
DAY = "2025-11-10"


def _hourly(dirs=None, speed=6.0, gust=9.0, rain=0.0):
    times = [f"{DAY}T{h:02d}:00" for h in range(24)]
    return {"time": times,
            "wind_direction_10m": dirs or [200.0] * 24,
            "wind_speed_10m": speed if isinstance(speed, list) else [speed] * 24,
            "wind_gusts_10m": gust if isinstance(gust, list) else [gust] * 24,
            "precipitation": rain if isinstance(rain, list) else [rain] * 24}


def _ridge_site():
    # valley floor at the centre, a 210 m ridge 600 m to the NW (as in test_lee_eddy)
    ux, uy = math.sin(math.radians(315)), math.cos(math.radians(315))
    dem = np.zeros((N, N))
    for r in range(N):
        for c in range(N):
            s = (c - N // 2) * CELL * ux + (N // 2 - r) * CELL * uy
            dem[r, c] = 100.0 + (0 if s <= 0 else 210 * s / 600 if s <= 600 else max(0.0, 210 - 0.1 * (s - 600)))
    return eddy.EddySite(dem, CELL, N // 2, N // 2)


def test_steady_wind_has_no_swings():
    assert hunt.wind_swings([200, 210, 190, 230, 220])[0] == 0


def test_swings_count_each_move_past_90_degrees_from_where_it_settled():
    swings, path = hunt.wind_swings([315, 320, 135, 140, 270])   # NW -> SE -> W
    assert swings == 2 and [round(d) for d in path] == [315, 135, 270]


def test_only_morning_and_evening_are_checked():
    dirs = [200.0] * 24
    dirs[12], dirs[13] = 20.0, 200.0   # swings at midday only
    assert hunt.day_conditions(_hourly(dirs), DAY, 6.75, 17.08, [], {}, SETTINGS) == {}


def test_sit_window_settings_decide_which_hours_are_checked():
    dirs = [200.0] * 24
    dirs[12], dirs[13] = 20.0, 200.0   # swings at midday by default...
    long_morning = {**SETTINGS, "sit_morning_after_sunrise_h": 7.0}   # ...inside a morning running to 13:59
    out = hunt.day_conditions(_hourly(dirs), DAY, 6.75, 17.08, [], {}, long_morning)
    assert out["morning"]["swirl"]["swings"] >= 2


def test_morning_swirl_is_flagged_with_its_directions():
    dirs = [200.0] * 24
    dirs[6], dirs[7], dirs[8] = 315.0, 135.0, 270.0   # morning window is 5-9 for a 6:45 sunrise
    out = hunt.day_conditions(_hourly(dirs), DAY, 6.75, 17.08, [], {}, SETTINGS)
    assert out["morning"]["swirl"]["swings"] >= 2 and "evening" not in out


def test_rain_and_wind_thresholds():
    rain = [0.0] * 24
    rain[16] = 5.08          # 0.2 in in the 4 PM hour (evening window 14-17)
    gust = [9.0] * 24
    gust[7] = 32.0
    out = hunt.day_conditions(_hourly(rain=rain, gust=gust), DAY, 6.75, 17.08, [], {}, SETTINGS)
    assert out["evening"]["rain"] == {"in_per_h": 0.2, "at": "4 PM"}
    assert out["morning"]["wind"]["gust_mph"] == 32 and out["morning"]["wind"]["at"] == "7 AM"
    assert "rain" not in out["morning"]
    light = [0.0] * 24
    light[16] = 1.0
    assert hunt.day_conditions(_hourly(rain=light), DAY, 6.75, 17.08, [], {}, SETTINGS) == {}


def test_lee_eddy_flags_the_stand_under_the_ridge():
    stands = [{"id": 1, "name": "Valley"}, {"id": 2, "name": "Flat"}]
    sites = {1: _ridge_site(), 2: None}
    out = hunt.day_conditions(_hourly([315.0] * 24, speed=14.0, gust=22.0), DAY, 6.75, 17.08, stands, sites, SETTINGS)
    assert out["morning"]["eddy_stands"] == [{"id": 1, "name": "Valley"}]
    calm = hunt.day_conditions(_hourly([315.0] * 24, speed=3.0, gust=5.0), DAY, 6.75, 17.08, stands, sites, SETTINGS)
    assert calm == {}
