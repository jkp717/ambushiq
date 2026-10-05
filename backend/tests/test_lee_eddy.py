import math

from app.forecast import eddy, scoring

N, CELL = 41, 60.0   # matches the 2.4 km context grid


def _ridge_dem(crest_m=600.0, height_m=210.0, toward_deg=315.0, n=N, cell=CELL):
    """Flat valley floor at the stand; ground rises linearly toward `toward_deg` to a crest
    `crest_m` away, then falls off gently on the far (windward) side."""
    ux, uy = math.sin(math.radians(toward_deg)), math.cos(math.radians(toward_deg))
    ctr = n // 2
    dem = []
    for r in range(n):
        row = []
        for c in range(n):
            s = (c - ctr) * cell * ux + (ctr - r) * cell * uy
            if s <= 0:
                z = 0.0
            elif s <= crest_m:
                z = height_m * s / crest_m
            else:
                z = max(0.0, height_m - 0.1 * (s - crest_m))
            row.append(100.0 + z)
        dem.append(row)
    return dem


def _terrain(**ridge):
    return {"flat": True, "context": {"dem": _ridge_dem(**ridge), "cell_m": CELL,
                                      "box_m": CELL * (N - 1), "grid_size": N}}


def _hour(**over):
    h = {"wind_dir": 315.0, "wind_speed": 14.0, "gust": 22.0, "solar": 300.0,
         "time_h": 12, "sunrise_h": 7.0, "sunset_h": 18.0, "temp_swing": None}
    h.update(over)
    return h


def test_strong_wind_over_steep_ridge_is_a_likely_eddy():
    e = eddy.detect_lee_eddy(_terrain(), 315, 14, 22)
    assert e["level"] == "likely"
    assert abs(e["crest_height_m"] - 210) <= 10 and abs(e["crest_dist_m"] - 600) <= 60
    assert e["near_ground_to_deg"] == 315


def test_ridge_beyond_the_800m_box_is_seen_through_the_context_grid():
    # crest 600 m away — outside the ±400 m analysis box
    assert eddy.detect_lee_eddy(_terrain(crest_m=600), 315, 14, 22) is not None


def test_eddy_reverses_near_ground_scent_toward_the_crest():
    sc = scoring.score_stand_hour({"terrain": _terrain()}, _hour())
    assert sc["lee_eddy"]["level"] == "likely"
    assert scoring.angle_diff(sc["scent_to_deg"], 315) < 5   # back up toward the NW, not SE


def test_light_wind_has_no_eddy():
    assert eddy.detect_lee_eddy(_terrain(), 315, 4, 5) is None


def test_gusts_alone_make_an_eddy_possible_without_flipping_scent():
    e = eddy.detect_lee_eddy(_terrain(), 315, 6, 12)
    assert e["level"] == "possible"
    sc = scoring.score_stand_hour({"terrain": _terrain()}, _hour(wind_speed=6.0, gust=12.0))
    assert scoring.angle_diff(sc["scent_to_deg"], 135) < 5


def test_gentle_slope_does_not_separate():
    assert eddy.detect_lee_eddy(_terrain(height_m=60), 315, 14, 22) is None   # 10% grade


def test_windward_stand_has_no_eddy():
    assert eddy.detect_lee_eddy(_terrain(), 135, 14, 22) is None


def test_no_terrain_has_no_eddy():
    assert eddy.detect_lee_eddy(None, 315, 14, 22) is None
    assert scoring.score_stand_hour({"terrain": None, "downhill_deg": 90}, _hour())["lee_eddy"] is None


def test_falls_back_to_the_analysis_grid_without_context():
    terrain = {"flat": True, "dem": _ridge_dem(crest_m=300, height_m=120, cell=20.0), "cell_m": 20.0}
    assert eddy.detect_lee_eddy(terrain, 315, 14, 22)["level"] == "likely"


def test_explanation_names_direction_height_and_wind():
    e = eddy.detect_lee_eddy(_terrain(), 315, 14, 22)
    assert "NW wind (14 mph, gusts 22)" in e["text"] and "ft above you" in e["text"]
    assert "% lee slope" in e["why"]


def test_breakdown_lists_the_eddy():
    det = scoring.score_with_breakdown({"terrain": _terrain()}, _hour())
    assert any(b["factor"] == "Lee eddy" for b in det["breakdown"])


def test_eddy_lowers_steadiness():
    plain = scoring.score_stand_hour({"terrain": None}, _hour())
    lee = scoring.score_stand_hour({"terrain": _terrain()}, _hour())
    assert lee["steadiness"] < plain["steadiness"]


def test_lee_zone_marks_the_stand_and_the_crest():
    z = eddy.lee_zone_mask(_terrain(), 315, 14, 22)
    assert z["grid"][N // 2][N // 2] == 1
    assert any(2 in row for row in z["grid"])
    assert eddy.lee_zone_mask(_terrain(), 315, 3, 4) is None


def test_vectors_carry_eddy_and_zone():
    v = scoring.stand_hour_vectors({"terrain": _terrain()}, _hour())
    assert v["lee_eddy"]["level"] == "likely" and v["lee_zone"] is not None
