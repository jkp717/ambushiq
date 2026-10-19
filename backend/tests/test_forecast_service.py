from datetime import datetime, timedelta, timezone

from app.forecast import providers, service


def _hourly(n=48, **over):
    base = {
        "time": [f"2025-11-08T{h % 24:02d}:00" for h in range(n)],
        "wind_speed_10m": [5.0] * n,
        "wind_direction_10m": [180.0] * n,
        "wind_gusts_10m": [8.0] * n,
        "shortwave_radiation": [0.0] * n,
        "temperature_2m": [10.0] * n,
    }
    base.update(over)
    return base


def test_safety_defaults_fill_missing_wind_and_temperature():
    hourly = _hourly(
        wind_speed_10m=[4.0, None, None, 6.0] + [6.0] * 44,
        wind_direction_10m=[None, 90.0] + [90.0] * 46,
        temperature_2m=[10.0, None, 14.0] + [14.0] * 45,
    )
    fc = {"hourly": hourly, "daily": {"sunrise": [], "sunset": []}}
    service._apply_safety_defaults(fc)
    assert hourly["wind_speed_10m"][1] == 4.0 and hourly["wind_speed_10m"][2] == 4.0
    assert hourly["wind_direction_10m"][0] == 90.0
    assert hourly["temperature_2m"][1] == 12.0
    assert None not in hourly["wind_speed_10m"] and None not in hourly["wind_direction_10m"]


def test_safety_defaults_all_wind_missing_becomes_zero():
    hourly = _hourly(wind_speed_10m=[None] * 48, wind_direction_10m=[None] * 48)
    service._apply_safety_defaults({"hourly": hourly, "daily": {"sunrise": [], "sunset": []}})
    assert set(hourly["wind_speed_10m"]) == {0.0}
    assert set(hourly["wind_gusts_10m"]) == {8.0}


def test_rolling_swing_sees_upcoming_night_and_never_returns_zero_for_missing():
    temps = [10.0] * 12 + [18.0] * 12 + [2.0] * 24  # cold night after a warm day
    swing = service._temp_swing_rolling({"temperature_2m": temps})
    assert swing[30] == 16.0  # window still spans the 18 °C afternoon and the 2 °C night
    assert swing[47] == 0.0  # late window is entirely the cold night: no swing left
    short = service._temp_swing_rolling({"temperature_2m": [10.0, 12.0, 11.0]})
    assert short == [None, None, None]


def test_label_helpers_have_no_platform_specific_formats():
    dt = datetime(2025, 11, 8, 7, 0)
    assert service.format_day_label(dt) == "Sat Nov 8"
    assert service.format_hour_label(dt) == "7 am"
    assert service.format_hour_label(dt, lower=False) == "7 AM"
    assert service.format_hour_label(datetime(2025, 11, 8, 15, 0)) == "3 pm"


def test_station_pressure_reduced_to_sea_level():
    assert service.msl_from_station(1013.25, 0) == 1013.25
    msl = service.msl_from_station(950.0, 500)  # ~500 m station reads ~950 hPa
    assert 1005 < msl < 1015
    assert service.msl_from_station(None, 500) is None
    assert service.msl_from_station(950.0, None) is None


def test_pressure_series_prefers_msl_then_reduces_surface():
    hourly = {"time": ["a", "b", "c"], "pressure_msl": [1015.0, None, None],
              "surface_pressure": [950.0, 950.0, None]}
    out = service.pressure_msl_series(hourly, 500)
    assert out[0] == 1015.0
    assert 1005 < out[1] < 1015
    assert out[2] is None
    assert service.pressure_msl_series(hourly, None)[1] is None


def _multi_day_hourly(start_day, n_days, **field_overrides):
    times = [f"2025-11-{start_day + d:02d}T{h:02d}:00" for d in range(n_days) for h in range(24)]
    n = len(times)
    base = {
        "time": times,
        "wind_speed_10m": [5.0] * n,
        "wind_direction_10m": [180.0] * n,
        "wind_gusts_10m": [8.0] * n,
        "shortwave_radiation": [100.0] * n,
        "temperature_2m": [10.0] * n,
        "cloud_cover": [20.0] * n,
        "surface_pressure": [1000.0] * n,
        "pressure_msl": [1015.0] * n,
        "precipitation": [0.0] * n,
        "dew_point_2m": [5.0] * n,
    }
    base.update(field_overrides)
    return base


def _multi_day_daily(start_day, n_days):
    return {
        "sunrise": [f"2025-11-{start_day + d:02d}T06:30" for d in range(n_days)],
        "sunset": [f"2025-11-{start_day + d:02d}T18:00" for d in range(n_days)],
    }


def test_hourly_day_count_counts_distinct_dates():
    assert service._hourly_day_count(_multi_day_hourly(1, 3)) == 3


def test_extend_with_secondary_appends_missing_trailing_days():
    primary_hourly = _multi_day_hourly(1, 3)
    original_times = list(primary_hourly["time"])
    primary_daily = _multi_day_daily(1, 3)
    primary_daily["source"] = ["Primary"] * 3
    forecast = {"hourly": primary_hourly, "daily": primary_daily}
    secondary_forecast = {"hourly": _multi_day_hourly(1, 6), "daily": _multi_day_daily(1, 6)}

    service._extend_with_secondary(forecast, secondary_forecast, days=6, secondary_label="Secondary")

    assert forecast["hourly"]["time"][:len(original_times)] == original_times
    assert service._hourly_day_count(forecast["hourly"]) == 6
    assert forecast["daily"]["source"] == ["Primary"] * 3 + ["Secondary"] * 3
    assert len(forecast["daily"]["sunrise"]) == 6 and len(forecast["daily"]["sunset"]) == 6


def test_extend_with_secondary_is_noop_when_primary_already_covers_days():
    primary_hourly = _multi_day_hourly(1, 6)
    primary_daily = _multi_day_daily(1, 6)
    primary_daily["source"] = ["Primary"] * 6
    forecast = {"hourly": primary_hourly, "daily": primary_daily}
    secondary_forecast = {"hourly": _multi_day_hourly(1, 10), "daily": _multi_day_daily(1, 10)}
    original_len = len(primary_hourly["time"])

    service._extend_with_secondary(forecast, secondary_forecast, days=6, secondary_label="Secondary")

    assert len(forecast["hourly"]["time"]) == original_len
    assert len(forecast["daily"]["sunrise"]) == 6


def test_extend_with_secondary_leaves_gap_when_secondary_also_short():
    primary_hourly = _multi_day_hourly(1, 3)
    primary_daily = _multi_day_daily(1, 3)
    primary_daily["source"] = ["Primary"] * 3
    forecast = {"hourly": primary_hourly, "daily": primary_daily}
    secondary_forecast = {"hourly": _multi_day_hourly(1, 5), "daily": _multi_day_daily(1, 5)}

    service._extend_with_secondary(forecast, secondary_forecast, days=14, secondary_label="Secondary")

    assert service._hourly_day_count(forecast["hourly"]) == 5
    assert len(forecast["daily"]["sunrise"]) == 5
    assert forecast["daily"]["source"] == ["Primary"] * 3 + ["Secondary"] * 2


def test_extend_with_secondary_fills_all_fields_not_just_hard_required():
    primary_hourly = _multi_day_hourly(1, 1)
    primary_daily = _multi_day_daily(1, 1)
    primary_daily["source"] = ["Primary"]
    forecast = {"hourly": primary_hourly, "daily": primary_daily}
    secondary_forecast = {
        "hourly": _multi_day_hourly(1, 2, dew_point_2m=[3.3] * 48, cloud_cover=[55.0] * 48),
        "daily": _multi_day_daily(1, 2),
    }

    service._extend_with_secondary(forecast, secondary_forecast, days=2, secondary_label="Secondary")

    assert forecast["hourly"]["dew_point_2m"][-1] == 3.3
    assert forecast["hourly"]["cloud_cover"][-1] == 55.0


def _partial_day_hourly(start_hour, n_hours, day="2025-11-08"):
    n = n_hours
    return {
        "time": [f"{day}T{start_hour + i:02d}:00" for i in range(n)],
        "wind_speed_10m": [5.0] * n,
        "wind_direction_10m": [180.0] * n,
        "wind_gusts_10m": [8.0] * n,
        "shortwave_radiation": [100.0] * n,
        "temperature_2m": [10.0] * n,
    }


def test_pad_to_local_midnight_prepends_none_rows_before_first_hour():
    hourly = _partial_day_hourly(14, 10)
    service._pad_to_local_midnight(hourly)
    assert len(hourly["time"]) == 24
    assert hourly["time"][0] == "2025-11-08T00:00"
    assert hourly["time"][14] == "2025-11-08T14:00"
    assert all(v is None for v in hourly["wind_speed_10m"][:14])
    assert hourly["wind_speed_10m"][14] == 5.0


def test_pad_to_local_midnight_is_noop_when_already_at_midnight():
    hourly = _hourly(24)
    original = {k: list(v) for k, v in hourly.items()}
    service._pad_to_local_midnight(hourly)
    assert hourly == original


def test_pad_to_local_midnight_then_safety_defaults_fills_leading_gap():
    hourly = _partial_day_hourly(14, 10)
    service._pad_to_local_midnight(hourly)
    fc = {"hourly": hourly, "daily": {"sunrise": ["2025-11-08T06:30"], "sunset": ["2025-11-08T18:00"]}}
    service._apply_safety_defaults(fc)
    assert None not in hourly["wind_speed_10m"] and None not in hourly["wind_direction_10m"]
    assert None not in hourly["temperature_2m"] and None not in hourly["shortwave_radiation"]
    assert hourly["wind_speed_10m"][0] == 5.0  # leading gap carried back from first known hour


def test_nws_interval_precip_is_split_across_hours():
    start = datetime(2025, 11, 8, 0, tzinfo=timezone.utc)
    raw = [{"validTime": "2025-11-08T00:00:00+00:00/PT6H", "value": 6.0}]
    hours = [start + timedelta(hours=i) for i in range(8)]
    per_hour = providers._nws_expand_series(raw, hours, per_hour=True)
    assert per_hour[:6] == [1.0] * 6 and per_hour[6:] == [None, None]
    assert sum(v for v in per_hour if v is not None) == 6.0
    repeated = providers._nws_expand_series(raw, hours)
    assert repeated[:6] == [6.0] * 6



# ---------- day_weather: the map day strip's icon ----------

def _wx_day(cloud=None, precip=None, temp=None):
    times = [f"2026-10-14T{h:02d}:00" for h in range(24)]
    hourly = {"time": times}
    for name, v in (("cloud_cover", cloud), ("precipitation", precip), ("temperature_2m", temp)):
        if v is not None:
            hourly[name] = v if isinstance(v, list) else [v] * 24
    return service.day_weather(hourly, list(range(24)), 7.0, 18.5)


def test_day_weather_icons_by_sky_and_rain():
    assert _wx_day(cloud=10, precip=0.0, temp=15)["icon"] == "sun"
    assert _wx_day(cloud=40, precip=0.0, temp=15)["icon"] == "partly"
    assert _wx_day(cloud=90, precip=0.0, temp=15)["icon"] == "cloud"
    assert _wx_day(cloud=90, precip=0.05, temp=15)["icon"] == "drizzle"     # 1.2 mm ≈ 0.05 in over the day
    assert _wx_day(cloud=90, precip=0.2, temp=15)["icon"] == "rain"         # 4.8 mm ≈ 0.19 in
    assert _wx_day(cloud=90, precip=0.2, temp=-2)["icon"] == "snow"


def test_day_weather_clouds_count_daylight_only():
    night_overcast = [100 if h < 7 or h > 18 else 5 for h in range(24)]
    assert _wx_day(cloud=night_overcast, precip=0.0)["icon"] == "sun"


def test_day_weather_text_is_imperial():
    wx = _wx_day(cloud=40, precip=0.05, temp=[10 + h / 2 for h in range(24)])
    assert wx["high_f"] == round(21.5 * 9 / 5 + 32) and wx["low_f"] == 50
    assert wx["text"] == f"Drizzle · high {wx['high_f']}°F · 0.05 in rain"
    assert _wx_day(cloud=40, temp=15)["text"] == "Partly cloudy · high 59°F"


def test_day_weather_without_data_has_no_icon():
    wx = _wx_day()
    assert wx["icon"] is None and wx["text"] == "" and wx["rain_in"] is None


# ---------- hour_weather: the map shelf's hourly cards ----------

def _wx_hour(i=12, sr=7.0, ss=18.5, pressures=None, **series):
    times = [f"2026-10-14T{h:02d}:00" for h in range(24)]
    hourly = {"time": times}
    for name, v in series.items():
        hourly[name] = v if isinstance(v, list) else [v] * 24
    return service.hour_weather(hourly, i, sr, ss, pressures or [None] * 24)


def test_hour_weather_icons_match_the_day_thresholds_and_go_dark_at_night():
    assert _wx_hour(cloud_cover=10, precipitation=0.0, temperature_2m=15)["icon"] == "sun"
    assert _wx_hour(cloud_cover=40, precipitation=0.0, temperature_2m=15)["icon"] == "partly"
    assert _wx_hour(cloud_cover=90, precipitation=0.5, temperature_2m=15)["icon"] == "drizzle"   # 0.02 in
    assert _wx_hour(cloud_cover=90, precipitation=3.0, temperature_2m=15)["icon"] == "rain"      # 0.12 in
    assert _wx_hour(cloud_cover=90, precipitation=0.5, temperature_2m=-1)["icon"] == "snow"
    assert _wx_hour(i=22, cloud_cover=10, precipitation=0.0)["icon"] == "night"
    assert _wx_hour(i=5, cloud_cover=40, precipitation=0.0)["icon"] == "night-partly"
    assert _wx_hour(i=22, cloud_cover=90, precipitation=0.0)["icon"] == "cloud"


def test_hour_weather_values_are_imperial():
    wx = _wx_hour(temperature_2m=10.0, wind_speed_10m=6.6, wind_direction_10m=212.4, wind_gusts_10m=14.2,
                  precipitation=1.27, cloud_cover=50)
    assert (wx["temp_f"], wx["wind_mph"], wx["wind_dir"], wx["gust_mph"], wx["rain_in"]) == (50, 7, 212, 14, 0.05)


def test_hour_weather_pressure_trend_over_three_hours():
    rising = [1010.0 + h * 0.5 for h in range(24)]          # +1.5 hPa / 3 h ≈ +0.044 inHg
    falling = [1020.0 - h * 0.5 for h in range(24)]
    steady = [1015.0 + (h % 2) * 0.2 for h in range(24)]
    assert _wx_hour(pressures=rising)["pressure_trend"] == "rising"
    assert _wx_hour(pressures=falling)["pressure_trend"] == "falling"
    assert _wx_hour(pressures=steady)["pressure_trend"] == "steady"
    assert _wx_hour(pressures=rising)["pressure_inhg"] == round(1016.0 * service.HPA_TO_INHG, 2)


def test_hour_weather_without_data_is_all_none():
    wx = _wx_hour()
    assert all(v is None for v in wx.values())
