"""Property-wide drainage (regions/drainage.py): the map layer, stand terrain read from the property
grid, and the automatic fallback analysis for stands the grid can't cover."""
import asyncio
import math

import numpy as np
import pytest

import app.main  # noqa: F401  (imports every router/model so create_all sees all tables)
from app.core.database import Base, engine
from app.forecast.eddy import Grid
from app.regions import drainage, terrain
from app.regions.models import Region
from app.stands import service as stand_service
from app.stands.models import Stand
from app.stands.terrain import M_PER_DEG_LAT
from sqlalchemy.orm import Session

N = 161                   # 161 x 30 m = 4.8 km box
CELL = 30.0
CLAT, CLON = 34.7, -92.3


def _valley_grid(source="USGS 3DEP", key=(1, "t1")) -> Grid:
    """A V-shaped valley running north-south down the middle column, falling toward the south."""
    rows, cols = np.mgrid[0:N, 0:N].astype(np.float64)
    dem = 300.0 + 0.25 * np.abs(cols - N // 2) * CELL + 0.02 * (N - rows) * CELL
    half_lat = (N - 1) * CELL / 2 / M_PER_DEG_LAT
    half_lon = (N - 1) * CELL / 2 / (M_PER_DEG_LAT * math.cos(math.radians(CLAT)))
    return Grid(dem=dem, cell_m=CELL, north=CLAT + half_lat, south=CLAT - half_lat,
                west=CLON - half_lon, east=CLON + half_lon, key=key, source=source)


def _lon_at(grid, col):
    return grid.west + col / (N - 1) * (grid.east - grid.west)


@pytest.fixture()
def grid(monkeypatch):
    g = _valley_grid()
    drainage._analysis_cache.clear()
    monkeypatch.setattr(terrain, "load_grid", lambda region_id: g)
    monkeypatch.setattr(terrain, "_stand_points", lambda region_id: [("A", CLAT, CLON), ("B", CLAT + 0.002, CLON + 0.002)])
    return g


def test_layer_is_brightest_along_the_valley_floor(grid):
    layer = drainage.drainage_layer(1, 400.0)
    rows = [[int(ch) for ch in row] for row in layer["rows"]]
    mid = len(rows) // 2
    lon_step = (grid.east - grid.west) / (N - 1)
    c0 = round((layer["bounds"][0][1] - grid.west) / lon_step + 0.5)   # grid column of the crop's west edge
    assert c0 + int(np.argmax(rows[mid])) == N // 2                     # deepest shade sits on the valley floor
    assert max(rows[mid]) == 9 and rows[mid][0] <= 6 and rows[mid][-1] <= 6   # side slopes far lighter


def test_layer_covers_the_stands_plus_the_margin_and_grows_with_it(grid):
    small = drainage.drainage_layer(1, 200.0)
    big = drainage.drainage_layer(1, 600.0)
    (s, w), (n, e) = small["bounds"]
    assert s < CLAT and n > CLAT + 0.002 and w < CLON and e > CLON + 0.002
    margin_deg = 200.0 / M_PER_DEG_LAT
    assert n - (CLAT + 0.002) == pytest.approx(margin_deg, abs=2 * CELL / M_PER_DEG_LAT)
    assert len(big["rows"]) > len(small["rows"]) and len(big["rows"][0]) > len(small["rows"][0])


def test_layer_is_clipped_to_the_grid(grid):
    layer = drainage.drainage_layer(1, 50_000.0)
    assert len(layer["rows"]) == N and len(layer["rows"][0]) == N


def test_no_layer_without_a_usable_grid(monkeypatch):
    monkeypatch.setattr(terrain, "load_grid", lambda region_id: None)
    assert drainage.drainage_layer(1, 400.0) is None
    monkeypatch.setattr(terrain, "load_grid", lambda region_id: _valley_grid(source="Open-Meteo"))
    assert drainage.drainage_layer(1, 400.0) is None


def test_accumulation_is_computed_once_per_grid_download(grid, monkeypatch):
    first = drainage.grid_analysis(grid)
    monkeypatch.setattr(drainage.rd, "flow_accumulation", lambda *a, **k: pytest.fail("recomputed"))
    assert drainage.grid_analysis(grid) is first


def test_stand_terrain_reads_direction_and_channel_from_the_property_grid(grid):
    on_floor = drainage.stand_terrain(grid, CLAT, _lon_at(grid, N // 2))
    on_slope = drainage.stand_terrain(grid, CLAT, _lon_at(grid, N // 2 + 20))   # east side, faces west
    assert on_floor["basis"] == "property" and on_floor["source"] == drainage.PROPERTY_SOURCE_LABEL
    assert 225 <= on_slope["downhill_deg"] <= 315
    assert 150 <= on_floor["drainage_deg"] <= 210                     # the valley drains south
    # the whole valley above it funnels past the floor: a full-strength channel
    assert on_floor["channel_strength"] == 1.0 >= on_slope["channel_strength"]
    assert not on_floor["flat"] and on_floor["grid_size"] == len(on_floor["dem"]) == len(on_floor["acc"])


def test_stand_terrain_needs_a_usgs_grid_and_a_point_well_inside_it(grid):
    assert drainage.stand_terrain(grid, grid.north - 0.001, CLON) is None          # near the edge
    assert drainage.stand_terrain(_valley_grid(source="Open-Meteo"), CLAT, CLON) is None
    assert drainage.stand_terrain(None, CLAT, CLON) is None


def test_with_property_terrain_tags_where_each_stands_terrain_came_from(grid):
    own = {"source": "USGS 3DEP", "downhill_deg": 10}
    out = drainage.with_property_terrain(1, [
        {"id": 1, "lat": CLAT, "lon": CLON, "terrain": own},
        {"id": 2, "lat": grid.north - 0.001, "lon": CLON, "terrain": own},
        {"id": 3, "lat": grid.north - 0.001, "lon": CLON, "terrain": None},
    ])
    assert out[0]["terrain"]["basis"] == "property"
    assert out[1]["terrain"] == {**own, "basis": "stand"}
    assert out[2]["terrain"] is None


# ---------- automatic fallback analysis ----------

@pytest.fixture()
def db_stands(grid, monkeypatch):
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        s.add(Region(id=1, name="R", lat=CLAT, lon=CLON, rut_peak_month=12, rut_peak_day=5,
                     property_timezone="America/Chicago", is_default=1, created_at=""))
        s.add(Stand(id=1, region_id=1, name="Inside", lat=CLAT, lon=CLON))
        s.add(Stand(id=2, region_id=1, name="Outside", lat=CLAT + 0.2, lon=CLON))
        s.add(Stand(id=3, region_id=1, name="Outside analysed", lat=CLAT + 0.2, lon=CLON, terrain_json="{}"))
        s.commit()
    analysed = []

    async def fake_analyze(stand_id, progress_callback=None):
        analysed.append(stand_id)

    monkeypatch.setattr(stand_service, "analyze_and_store", fake_analyze)
    stand_service._auto_running.clear()
    return analysed


def test_only_stands_the_grid_cant_cover_are_analysed_on_save(db_stands):
    async def go():
        started = [stand_service.auto_analyze_if_outside(sid, 1) for sid in (1, 2)]
        await asyncio.gather(*stand_service._auto_tasks)
        return started
    assert asyncio.run(go()) == [False, True]
    assert db_stands == [2]


def test_after_a_grid_download_uncovered_stands_without_their_own_analysis_get_one(db_stands):
    async def go():
        n = stand_service.auto_analyze_uncovered(1)
        await asyncio.gather(*stand_service._auto_tasks)
        return n
    assert asyncio.run(go()) == 1
    assert db_stands == [2]      # 1 is covered, 3 already has its own analysis
