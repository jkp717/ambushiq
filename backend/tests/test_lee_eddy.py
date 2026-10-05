import math

import numpy as np

from app.forecast import eddy, scoring

N, CELL = 81, 50.0   # a 4 km property grid
LAT0, LON0 = 34.7, -92.3


def _ridge_dem(crest_m=600.0, height_m=210.0, toward_deg=315.0, n=N, cell=CELL, center=None):
    """Flat valley floor around `center` (row, col); ground rises linearly toward `toward_deg`
    to a crest `crest_m` away, then falls off gently on the far (windward) side."""
    ux, uy = math.sin(math.radians(toward_deg)), math.cos(math.radians(toward_deg))
    cr, cc = center if center is not None else (n // 2, n // 2)
    dem = np.zeros((n, n))
    for r in range(n):
        for c in range(n):
            s = (c - cc) * cell * ux + (cr - r) * cell * uy
            z = 0.0 if s <= 0 else height_m * s / crest_m if s <= crest_m else max(0.0, height_m - 0.1 * (s - crest_m))
            dem[r, c] = 100.0 + z
    return dem


def _grid(key=(), **ridge):
    dem = _ridge_dem(**ridge)
    span_lat = CELL * (N - 1) / 111320.0
    span_lon = span_lat / math.cos(math.radians(LAT0))
    return eddy.Grid(dem=dem, cell_m=CELL, north=LAT0 + span_lat / 2, south=LAT0 - span_lat / 2,
                     west=LON0 - span_lon / 2, east=LON0 + span_lon / 2, key=key)


def _site(**ridge):
    return eddy.EddySite(_ridge_dem(**ridge), CELL, N // 2, N // 2)


def _hour(**over):
    h = {"wind_dir": 315.0, "wind_speed": 14.0, "gust": 22.0, "solar": 300.0,
         "time_h": 12, "sunrise_h": 7.0, "sunset_h": 18.0, "temp_swing": None}
    h.update(over)
    return h


def test_strong_wind_over_steep_ridge_is_a_likely_eddy():
    e = eddy.detect_lee_eddy(_site(), 315, 14, 22)
    assert e["level"] == "likely"
    assert abs(e["crest_height_m"] - 210) <= 10 and abs(e["crest_dist_m"] - 600) <= 60
    assert e["near_ground_to_deg"] == 315


def test_stand_reads_its_own_point_in_the_property_grid():
    # ridge centered on an off-center stand location — the stand's grid point sees it
    grid = _grid(center=(30, 50))
    lat = grid.north - 30 / (N - 1) * (grid.north - grid.south)
    lon = grid.west + 50 / (N - 1) * (grid.east - grid.west)
    site = eddy.site_for(grid, {"lat": lat, "lon": lon, "terrain": None})
    assert abs(site.row - 30) < 1e-6 and abs(site.col - 50) < 1e-6
    assert eddy.detect_lee_eddy(site, 315, 14, 22)["level"] == "likely"


def test_stand_outside_the_property_grid_falls_back_to_its_own_terrain():
    terrain = {"dem": _ridge_dem(crest_m=300, height_m=120, n=41, cell=20.0).tolist(), "cell_m": 20.0}
    far = {"lat": LAT0 + 1.0, "lon": LON0, "terrain": terrain}
    site = eddy.site_for(_grid(), far)
    assert site.cell_m == 20.0 and site.row == 20
    assert eddy.detect_lee_eddy(site, 315, 14, 22)["level"] == "likely"
    assert eddy.site_for(None, {"lat": LAT0, "lon": LON0, "terrain": None}) is None


def test_min_inset_pushes_edge_stands_to_their_fallback():
    grid = _grid()
    near_edge = {"lat": grid.north - 0.001, "lon": LON0, "terrain": None}
    assert eddy.site_for(grid, near_edge) is not None
    assert eddy.site_for(grid, near_edge, min_inset_m=1500) is None


def test_eddy_reverses_near_ground_scent_toward_the_crest():
    sc = scoring.score_stand_hour({"terrain": None}, _hour(), eddy_site=_site())
    assert sc["lee_eddy"]["level"] == "likely"
    assert scoring.angle_diff(sc["scent_to_deg"], 315) < 5   # back up toward the NW, not SE


def test_light_wind_has_no_eddy():
    assert eddy.detect_lee_eddy(_site(), 315, 4, 5) is None


def test_gusts_alone_make_an_eddy_possible_without_flipping_scent():
    assert eddy.detect_lee_eddy(_site(), 315, 6, 12)["level"] == "possible"
    sc = scoring.score_stand_hour({"terrain": None}, _hour(wind_speed=6.0, gust=12.0), eddy_site=_site())
    assert scoring.angle_diff(sc["scent_to_deg"], 135) < 5


def test_gentle_slope_does_not_separate():
    assert eddy.detect_lee_eddy(_site(height_m=60), 315, 14, 22) is None   # 10% grade


def test_windward_stand_has_no_eddy():
    assert eddy.detect_lee_eddy(_site(), 135, 14, 22) is None


def test_crest_beyond_the_walk_limit_is_ignored():
    # 600 m of flat ground, then a 50% face up to a 200 m crest 1 km upwind
    dem = _ridge_dem() * 0 + 100.0
    ux, uy, c = math.sin(math.radians(315)), math.cos(math.radians(315)), N // 2
    for r in range(N):
        for col in range(N):
            s = (col - c) * CELL * ux + (c - r) * CELL * uy
            dem[r, col] += min(200.0, max(0.0, (s - 600) * 0.5))
    site = eddy.EddySite(dem, CELL, c, c)
    assert eddy.detect_lee_eddy(site, 315, 14, 22)["level"] == "likely"
    assert eddy.detect_lee_eddy(site, 315, 14, 22, {"max_walk_m": 500}) is None


def test_no_terrain_has_no_eddy():
    assert eddy.detect_lee_eddy(None, 315, 14, 22) is None
    assert scoring.score_stand_hour({"terrain": None, "downhill_deg": 90}, _hour())["lee_eddy"] is None


def test_stand_terrain_is_the_default_site():
    terrain = {"flat": True, "dem": _ridge_dem(crest_m=300, height_m=120, n=41, cell=20.0).tolist(), "cell_m": 20.0}
    assert scoring.score_stand_hour({"terrain": terrain}, _hour())["lee_eddy"]["level"] == "likely"


def test_explanation_names_direction_height_and_wind():
    e = eddy.detect_lee_eddy(_site(), 315, 14, 22)
    assert "NW wind (14 mph, gusts 22)" in e["text"] and "ft above you" in e["text"]
    assert "% lee slope" in e["why"]


def test_breakdown_lists_the_eddy():
    det = scoring.score_with_breakdown({"terrain": None}, _hour(), eddy_site=_site())
    assert any(b["factor"] == "Lee eddy" for b in det["breakdown"])


def test_eddy_lowers_steadiness():
    plain = scoring.score_stand_hour({"terrain": None}, _hour())
    lee = scoring.score_stand_hour({"terrain": None}, _hour(), eddy_site=_site())
    assert lee["steadiness"] < plain["steadiness"]


def test_lee_zone_marks_the_valley_and_the_crest():
    z = eddy.lee_zone_mask(_grid(), 315, 14, 22)
    assert len(z["rows"]) == N and all(len(r) == N for r in z["rows"])
    assert z["rows"][N // 2][N // 2] == "1"
    assert any("2" in row for row in z["rows"])
    (s, w), (n, e) = z["bounds"]
    assert s < LAT0 < n and w < LON0 < e
    assert eddy.lee_zone_mask(_grid(), 315, 3, 4) is None
    assert eddy.lee_zone_mask(None, 315, 14, 22) is None


def test_lee_zone_agrees_with_point_detection():
    grid = _grid()
    z = eddy.lee_zone_mask(grid, 315, 14, 22)
    for r in range(0, N, 8):
        for c in range(0, N, 8):
            hit = eddy.detect_lee_eddy(eddy.EddySite(grid.dem, CELL, r, c), 315, 14, 22) is not None
            assert hit == (z["rows"][r][c] != "0") or z["rows"][r][c] == "2"


def test_lee_zone_mask_is_cached_per_grid_and_direction():
    eddy._mask_cache.clear()
    grid = _grid(key=("r", 1))
    eddy.lee_zone_mask(grid, 315, 14, 22)
    eddy.lee_zone_mask(grid, 315.2, 20, 30)   # same whole-degree direction, different speed
    assert len(eddy._mask_cache) == 1


def test_vectors_carry_the_eddy():
    v = scoring.stand_hour_vectors({"terrain": None}, _hour(), eddy_site=_site())
    assert v["lee_eddy"]["level"] == "likely" and "lee_zone" not in v
