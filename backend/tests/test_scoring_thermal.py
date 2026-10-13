from app.forecast import scoring

TERRAIN = {"downhill_deg": 90, "drainage_deg": 180, "channel_strength": 0.5, "flat": False}


def _hour(**over):
    h = {"wind_dir": 270.0, "wind_speed": 3.0, "gust": 4.0, "solar": 0.0,
         "time_h": 6, "sunrise_h": 7.0, "sunset_h": 18.0, "temp_swing": None}
    h.update(over)
    return h


def test_stand_without_terrain_has_no_thermal_and_follows_the_wind():
    stand = {"terrain": None, "downhill_deg": None}
    sc = scoring.score_stand_hour(stand, _hour())
    assert sc["thermal_known"] is False and sc["tw"] == 0.0
    assert sc["thermal_to_deg"] is None and sc["drainage_deg"] is None
    assert sc["scent_to_deg"] == 90  # wind from 270 blows toward 90
    vec = scoring.stand_hour_vectors(stand, _hour())
    assert vec["thermal_to_deg"] is None and vec["thermal_strength"] == 0.0


def test_flat_terrain_is_treated_as_unknown():
    stand = {"terrain": {**TERRAIN, "flat": True}}
    assert scoring.score_stand_hour(stand, _hour())["thermal_known"] is False


def test_manual_downhill_bearing_counts_as_known():
    stand = {"terrain": None, "downhill_deg": 200}
    sc = scoring.score_stand_hour(stand, _hour())
    assert sc["thermal_known"] is True and sc["drainage_deg"] == 200


def test_known_terrain_pulls_scent_toward_drainage_at_dawn():
    sc = scoring.score_stand_hour({"terrain": TERRAIN}, _hour(wind_speed=1.0))
    assert sc["thermal_phase"] == "sinking" and sc["thermal_to_deg"] == 180
    assert 90 < sc["scent_to_deg"] <= 180


def test_bright_late_afternoon_is_rising_not_sinking():
    state = scoring.thermal_state(16, 380, 6.5, 18.25)
    assert state["phase"] == "rising" and state["uphill"] is True
    assert scoring.thermal_state(16, 50, 6.5, 18.25)["phase"] == "sinking"
    assert scoring.thermal_state(23, 0, 6.5, 18.25)["phase"] == "sinking"


def test_thermal_weight_is_capped():
    params = {"thermal_gain": 50.0, "wind_half_scale": 40.0}
    sc = scoring.score_stand_hour({"terrain": TERRAIN}, _hour(), params)
    assert sc["tw"] <= scoring.TW_MAX


def test_neutral_phase_hides_thermal_arrow():
    stand = {"terrain": TERRAIN}
    vec = scoring.stand_hour_vectors(stand, _hour(time_h=13, solar=50, sunrise_h=6.5, sunset_h=19.0))
    assert vec["thermal_phase"] == "neutral" and vec["thermal_to_deg"] is None


def test_gale_does_not_earn_thermal_predictability():
    stand = {"terrain": TERRAIN}
    calm = scoring.score_stand_hour(stand, _hour(wind_speed=3.0, gust=3.0))
    gale = scoring.score_stand_hour(stand, _hour(wind_speed=16.0, gust=16.0))
    assert calm["steadiness"] == gale["steadiness"]  # isolate the thermal term
    assert calm["conditions"] > gale["conditions"] + 0.05


# ---------- the switch follows the sun on the slope, not the clock ----------

SR, SS, LAT = 6.75, 17.1, 34.7          # a November day in central Arkansas
EAST, WEST = 90, 270


def _phase(t, solar, aspect=None, slope=40):
    ratio = scoring.slope_sun_ratio(t, SR, SS, LAT, slope, aspect) if aspect is not None else 1.0
    return scoring.thermal_state(t, solar, SR, SS, None, ratio)["phase"]


def test_slope_facing_the_low_sun_gets_more_of_it():
    assert scoring.slope_sun_ratio(SR + 1, SR, SS, LAT, 40, EAST) > 1.5
    assert scoring.slope_sun_ratio(SR + 1, SR, SS, LAT, 40, WEST) < 0.5
    assert scoring.slope_sun_ratio(SS - 1, SR, SS, LAT, 40, WEST) > 1.5   # afternoon: the other way round
    assert scoring.slope_sun_ratio(SR + 1, SR, SS, None, 40, EAST) == 1.0  # unknown latitude
    assert scoring.slope_sun_ratio(SR + 1, SR, SS, LAT, 0, EAST) == 1.0    # flat ground
    assert scoring.slope_sun_ratio(SR - 0.5, SR, SS, LAT, 40, EAST) == 1.0  # sun not up


def test_clear_morning_turns_uphill_by_sun_not_the_clock():
    assert _phase(SR + 1, 120) == "rising"                     # flat ground, ~1 h after sunrise
    assert _phase(SR + 0.75, 90, EAST) == "rising"             # sun-facing slope flips sooner
    assert _phase(SR + 0.75, 90) == "sinking"                  # ...than flat ground
    assert _phase(SR + 1, 120, WEST) == "sinking"              # a slope still in shade keeps draining
    assert _phase(SR - 0.5, 0, EAST) == "sinking"              # before sunrise it always drains


def test_cloudy_morning_holds_drainage():
    assert _phase(SR + 2.5, 40) == "sinking"                   # was neutral after sunrise + 2 h
    assert _phase(SR + 3.5, 40) == "neutral"                   # past the dawn window: no clean flow


def test_evening_shade_side_drains_first():
    assert _phase(SS - 2, 150, EAST) == "sinking"
    assert _phase(SS - 2, 150, WEST) == "rising"


def test_stand_scoring_uses_its_slope_and_latitude():
    east = {"lat": LAT, "terrain": {**TERRAIN, "downhill_deg": EAST, "slope_pct": 40}}
    west = {"lat": LAT, "terrain": {**TERRAIN, "downhill_deg": WEST, "slope_pct": 40}}
    hour = _hour(time_h=SR + 0.75, solar=90.0, sunrise_h=SR, sunset_h=SS)
    assert scoring.score_stand_hour(east, hour)["thermal_phase"] == "rising"
    assert scoring.score_stand_hour(west, hour)["thermal_phase"] == "sinking"
