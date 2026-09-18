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


def test_nws_interval_precip_is_split_across_hours():
    start = datetime(2025, 11, 8, 0, tzinfo=timezone.utc)
    raw = [{"validTime": "2025-11-08T00:00:00+00:00/PT6H", "value": 6.0}]
    hours = [start + timedelta(hours=i) for i in range(8)]
    per_hour = providers._nws_expand_series(raw, hours, per_hour=True)
    assert per_hour[:6] == [1.0] * 6 and per_hour[6:] == [None, None]
    assert sum(v for v in per_hour if v is not None) == 6.0
    repeated = providers._nws_expand_series(raw, hours)
    assert repeated[:6] == [6.0] * 6
