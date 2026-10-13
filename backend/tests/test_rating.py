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
    assert _rating(OFF_SEASON_DAY, WORST) <= 2        # displays as 1.5
    assert _rating(date(2026, 1, 20), PERFECT) <= 3   # "off-season" tops out around 3


def test_rating_is_the_displayed_score_rounded_half_up():
    # (score, displayed, rating) — 1.5 / 2.5 / 3.5 / 4.5 round up, never banker's-rounded
    for score, shown, expected in [(0.0, 1.0, 1), (0.1, 1.4, 1), (0.125, 1.5, 2), (0.375, 2.5, 3),
                                   (0.39, 2.6, 3), (0.4, 2.6, 3), (0.625, 3.5, 4), (0.875, 4.5, 5),
                                   (1.0, 5.0, 5)]:
        assert rating.score_out_of_5(score) == shown
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


def test_rain_is_reported_in_inches():
    wx = {**PERFECT, "rain_mm": 6.35}
    rain = next(b for b in rating.rate_day(PEAK_RUT_DAY, wx)["breakdown"] if b["factor"] == "Rain")
    assert rain["impact"].endswith("(0.25 in)")


# ---------- rut impact (Settings -> Daily rating) ----------

MILD = {**PERFECT, "pressure_inhg": 29.7, "wind_mph": 4.0}   # middling weather, so no factor sits at a cap


def _score(day, impact=None):
    return rating.rate_day(day, MILD, rut_impact=impact)["score"]


def test_default_rut_impact_keeps_the_original_calibration():
    r = rating.rate_day(OFF_SEASON_DAY, MILD)
    rut = r["rut"]["intensity"]
    weather = _score(OFF_SEASON_DAY, 0.0)                       # 0% impact = weather alone
    assert _score(OFF_SEASON_DAY) == _score(OFF_SEASON_DAY, 0.65)
    assert r["score"] == pytest.approx(weather * (0.35 + 0.65 * rut), abs=0.01)
    assert "rut impact 65%" in r["breakdown"][0]["impact"]


def test_zero_rut_impact_ignores_the_season():
    assert _score(OFF_SEASON_DAY, 0.0) == _score(PEAK_RUT_DAY, 0.0)


def test_full_rut_impact_holds_the_off_season_down_but_never_the_peak():
    rut_off = rating.rate_day(OFF_SEASON_DAY, MILD)["rut"]["intensity"]
    rut_peak = rating.rate_day(PEAK_RUT_DAY, MILD)["rut"]["intensity"]
    assert _score(OFF_SEASON_DAY, 1.0) == pytest.approx(_score(OFF_SEASON_DAY, 0.0) * rut_off, abs=0.01)
    assert _score(OFF_SEASON_DAY, 1.0) < _score(OFF_SEASON_DAY) < _score(OFF_SEASON_DAY, 0.0)
    assert _score(PEAK_RUT_DAY, 1.0) == pytest.approx(_score(PEAK_RUT_DAY, 0.0) * rut_peak, abs=0.01)
    assert rut_peak > 0.95                                       # so the peak is (all but) untouched


def test_rut_impact_is_clamped():
    assert _score(OFF_SEASON_DAY, -1) == _score(OFF_SEASON_DAY, 0.0)
    assert _score(OFF_SEASON_DAY, 5) == _score(OFF_SEASON_DAY, 1.0)
