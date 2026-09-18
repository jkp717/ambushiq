from datetime import datetime, timedelta, timezone

import pytest

from app.deer_ratings import rating
from app.forecast import scoring, service
from app.scouting import scoring as scout

SETTINGS = {"weight_corridor": 0.15, "falloff_corridor": 150, "weight_food": 0.15, "falloff_food": 200,
            "weight_bedding": 0.10, "falloff_bedding": 250, "weight_scrape": 0.12, "falloff_scrape": 100,
            "weight_rub": 0.10, "falloff_rub": 80}
STAND = {"id": 1, "lat": 34.70, "lon": -92.30, "terrain": None, "downhill_deg": 90, "deer_approach_deg": 270}
NOW = datetime(2025, 11, 1, tzinfo=timezone.utc)


def _rubs(n, age_days=0, kind="rub"):
    created = (NOW - timedelta(days=age_days)).isoformat()
    return [{"kind": kind, "lat": 34.70, "lon": -92.30, "is_active": True, "created_at": created} for _ in range(n)]


def _prox(sign, mult=None):
    return service.proximity_bonus(STAND, [], [], SETTINGS, sign=sign, multipliers=mult, now=NOW)


def test_stacked_sign_has_diminishing_returns_and_bounded_total():
    one, twelve = _prox(_rubs(1))["rub"], _prox(_rubs(12))["rub"]
    assert one == pytest.approx(0.10)                     # a single ideal rub is unchanged
    assert one < twelve <= 1.5 * 0.10 + 1e-9              # 12 rubs ≈ 1.5 rubs, not 12
    # rubs + scrapes together are bounded by 1.5 x their weights (0.10 + 0.12), however many are logged
    assert _prox(_rubs(12) + _rubs(12, kind="scrape"))["total"] <= 1.5 * (0.10 + 0.12) + 1e-9


def test_soft_cap_is_continuous_and_monotonic():
    xs = [i / 100 for i in range(0, 600)]
    ys = [service._soft_cap(x) for x in xs]
    assert all(b >= a for a, b in zip(ys, ys[1:]))
    assert max(abs(b - a) for a, b in zip(ys, ys[1:])) < 0.011
    assert ys[-1] < 1.5


def test_sign_freshness_decays_by_kind_with_a_floor():
    fresh, month, ancient = (_prox(_rubs(1, d, "scrape"))["scrape"] for d in (0, 21, 3000))
    assert month == pytest.approx(fresh * 0.5, rel=0.01)
    assert ancient == pytest.approx(fresh * service.SIGN_FRESHNESS_FLOOR, rel=0.01)
    assert _prox([{"kind": "rub", "lat": 34.7, "lon": -92.3, "is_active": True, "created_at": ""}])["rub"] \
        == pytest.approx(0.10)                             # unknown date counts as fresh


def test_season_multipliers_shift_weight_between_sign_and_food():
    pre = rating.phase_proximity_multipliers("early season / seeking")
    post = rating.phase_proximity_multipliers("post-rut")
    assert pre["scrape"] > 1 > post["scrape"]
    assert post["food"] > 1 and post["food"] > pre["food"]
    assert _prox(_rubs(1, kind="scrape"), pre)["scrape"] > _prox(_rubs(1, kind="scrape"), post)["scrape"]
    assert rating.phase_proximity_multipliers("post-rut", 0.0) == {k: 1.0 for k in pre}
    half = rating.phase_proximity_multipliers("post-rut", 0.5)
    assert half["food"] == pytest.approx(1.2)
    assert rating.phase_proximity_multipliers("unknown phase") == {k: 1.0 for k in pre}


HOUR = {"wind_dir": 90.0, "wind_speed": 6.0, "gust": 7.0, "solar": 0.0,   # wind FROM the east...
        "time_h": 6, "sunrise_h": 7.0, "sunset_h": 18.0, "temp_swing": None, "date": "2025-11-01"}


@pytest.mark.parametrize("floor,expected_ratio", [(0.0, 0.0), (0.4, 0.4), (1.0, 1.0)])
def test_scent_gate_floor_is_configurable(floor, expected_ratio):
    # No terrain, so scent simply follows the wind: wind FROM the east (90) blows scent to
    # the west (270), straight at deer approaching from the west — the worst case.
    stand = {**STAND, "downhill_deg": None, "deer_approach_deg": 270}
    bad = scoring.score_with_breakdown(stand, HOUR, scent_gate_floor=floor)
    ok = scoring.score_with_breakdown({**stand, "deer_approach_deg": None}, HOUR, scent_gate_floor=floor)
    assert bad["scent_score"] == 0.0
    assert bad["final_score"] == pytest.approx(ok["final_score"] * expected_ratio, abs=0.002)


def test_final_score_formula_matches_docstring():
    prox = {"total": 0.2}
    det = scoring.score_with_breakdown({**STAND, "deer_approach_deg": None}, HOUR, proximity=prox)
    base = scoring.score_stand_hour({**STAND, "deer_approach_deg": None}, HOUR)
    assert det["final_score"] == pytest.approx(base["conditions"] + 0.2, abs=0.002)


def test_period_windows_match_previous_day_ranking_math_and_are_shared():
    w = scoring.period_windows(7.3, 17.1)
    assert w == {"morning": (6, 10), "midday": (11, 13), "evening": (14, 17)}
    assert scoring.period_for_hour(10, w) == "morning" and scoring.period_for_hour(11, w) == "midday"
    assert scoring.period_for_hour(3, w) is None
    assert scoring.period_windows(6.5, 19.0) == scoring.DEFAULT_PERIOD_WINDOWS


def _ctx(sightings):
    return service.ScoringContext(
        settings=SETTINGS, thermal_params={}, zones=[], corridors=[], sign=[],
        sightings_by_stand={1: sightings}, camera_state={1: {"ready": True, "healthy": True, "reason": None}},
        camera_scoring_on=True, max_boost=15.0, max_penalty=15.0, lookback=10_000.0, saturation=3.0,
        utc_offset=-6 * 3600, tz_name="America/Chicago", scent_gate_floor=0.4,
        rut_peak=(12, 5), rut_strength=1.0)


def test_night_photos_are_dropped_from_camera_evidence():
    day = {"timestamp": "2025-12-04T18:00:00+00:00", "confidence_score": 0.9, "species": "white-tailed deer"}     # noon local
    dark = {"timestamp": "2025-12-05T00:30:00+00:00", "confidence_score": 0.9, "species": "white-tailed deer"}    # 6:30 PM local, after a ~5 PM sunset
    kept = _ctx([day, dark]).daylight_sightings(STAND)
    assert kept == [day]


def test_context_scores_include_season_and_use_one_pipeline():
    hour = {**HOUR, "date": "2025-11-10"}   # seeking phase for the default Dec-5 peak
    fresh = [{**sg, "created_at": datetime.now(timezone.utc).isoformat()} for sg in _rubs(3, kind="scrape")]

    def run(strength):
        ctx = _ctx([])
        ctx.sign, ctx.rut_strength = fresh, strength
        return ctx.score(STAND, hour, "morning", scoring.DEFAULT_PERIOD_WINDOWS)

    det, off = run(1.0), run(0.0)
    assert any("weighted for" in b["text"] for b in det["breakdown"] if b["factor"] == "Infrastructure proximity")
    assert not any("weighted for" in b["text"] for b in off["breakdown"])
    assert det["proximity_bonus"] != off["proximity_bonus"]


def test_scouting_norm_cap_tracks_weights_and_zero_confidence_is_respected():
    assert scout.proximity_norm_cap(SETTINGS) == pytest.approx(0.62)
    assert scout.proximity_norm_cap({**SETTINGS, "weight_food": 0.45}) == pytest.approx(0.92)
    recent = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    sg = {"lat": 34.7, "lon": -92.3, "timestamp": recent, "species": "white-tailed deer"}
    zero = scout.camera_confirmation_bonus(34.7, -92.3, [{**sg, "confidence_score": 0.0}])
    unknown = scout.camera_confirmation_bonus(34.7, -92.3, [{**sg, "confidence_score": None}])
    assert zero == pytest.approx(0.1 / 3.0) and unknown == pytest.approx(0.5 / 3.0)
