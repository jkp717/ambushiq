from datetime import date, timedelta

import pytest

from app.deer_ratings import rating

PERFECT = {"pressure_inhg": 30.2, "pressure_trend_inhg": 0.06, "wind_mph": 9.0, "rain_mm": 0.0,
           "day_high_f": 60, "baseline_f": 75, "dew_point_f": 40}
WORST = {"pressure_inhg": 29.0, "pressure_trend_inhg": 0.0, "wind_mph": 40.0, "rain_mm": 15.0,
         "day_high_f": 95, "baseline_f": 60, "dew_point_f": 75}

PEAK_RUT_DAY = date(2025, 11, 25)   # default Dec-5 peak minus the 10-day "hunt peak" offset
OFF_SEASON_DAY = date(2025, 7, 15)


def _rating(day, wx):
    return rating.rate_day(day, wx)["rating"]


def test_scenario_table_uses_the_full_scale():
    assert _rating(PEAK_RUT_DAY, PERFECT) == 5
    assert _rating(PEAK_RUT_DAY, WORST) == 2
    assert _rating(OFF_SEASON_DAY, PERFECT) == 3
    assert _rating(OFF_SEASON_DAY, WORST) == 1
    assert _rating(date(2026, 1, 20), PERFECT) <= 3   # "off-season" tops out around 3


def test_rating_bins_have_no_bankers_rounding():
    for score, expected in [(0.0, 1), (0.19, 1), (0.2, 2), (0.375, 2), (0.4, 3), (0.625, 4), (0.8, 5), (1.0, 5)]:
        assert rating.rating_from_score(score) == expected


@pytest.mark.parametrize("fn,lo,hi,step,max_jump", [
    (lambda x: rating.pressure_factor(x, None), 28.5, 31.5, 0.001, 0.01),
    (lambda x: rating.wind_factor(x), 0.0, 45.0, 0.01, 0.01),
    (lambda x: rating.rain_factor(x, 0.0), 0.0, 20.0, 0.01, 0.02),
    (lambda wind: rating.rain_factor(5.0, wind), 0.0, 30.0, 0.01, 0.01),  # wind blunting ramp
])
def test_factor_curves_have_no_steps(fn, lo, hi, step, max_jump):
    x, prev = lo, fn(lo)
    while x < hi:
        x += step
        cur = fn(x)
        assert abs(cur - prev) <= max_jump, f"jump {prev}->{cur} near {x}"
        prev = cur


def test_rut_peak_in_january_and_february_registers():
    for month, day in [(1, 15), (2, 1)]:
        peak = date(2026, month, day)
        hunt_peak = peak - timedelta(days=10)
        inten, phase = rating.rut_intensity(hunt_peak, month, day)
        assert inten > 0.95 and phase.startswith("rut")
        assert rating.rut_intensity(hunt_peak - timedelta(days=15), month, day)[0] > 0.5
        # a date in the previous calendar year still sees the upcoming peak
        assert rating.rut_intensity(date(2025, 12, 20), 1, 15)[0] > 0.5


def test_feb_29_peak_in_a_common_year_is_clamped_not_reset_to_december():
    inten, _ = rating.rut_intensity(date(2025, 2, 18), 2, 29)   # hunt peak = Feb 28 - 10 days
    assert inten > 0.95


def test_phase_labels_agree_with_the_curve():
    day0 = PEAK_RUT_DAY
    by_phase: dict[str, list[float]] = {}
    for off in range(-120, 121):
        inten, phase = rating.rut_intensity(day0 + timedelta(days=off))
        by_phase.setdefault(phase, []).append(inten)
    assert max(by_phase["pre-season"]) < 0.32 and max(by_phase["off-season"]) < 0.32
    assert max(by_phase["breeding peak / lockdown"]) < max(by_phase["rut (chasing / peak daylight)"])
    assert min(by_phase["rut (chasing / peak daylight)"]) > max(by_phase["pre-season"])


def test_heavy_rain_still_caps_the_score():
    wx = {**PERFECT, "rain_mm": 9.0}
    assert rating.rate_day(PEAK_RUT_DAY, wx)["score"] <= rating.HEAVY_RAIN_SCORE_CAP
