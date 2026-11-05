import asyncio
import json

import pytest
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

import app.main  # noqa: F401  (registers every model so create_all sees all tables)
from app.core.database import Base, engine
from app.osmtrails import importer, service
from app.osmtrails.models import OsmExtract, OsmTrailCell
from app.regions.models import Region

# A square "state" around the test spot (Pope County, IL)
STATE = {"type": "MultiPolygon", "coordinates": [[[[-89.5, 37.0], [-88.0, 37.0], [-88.0, 38.0], [-89.5, 38.0], [-89.5, 37.0]]]]}


@pytest.fixture()
def db(monkeypatch):
    """Fresh tables; worker threads run inline (the in-memory SQLite test DB is per thread)."""
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        for model in (OsmTrailCell, OsmExtract, Region):
            s.execute(delete(model))
        s.commit()
    service.invalidate()

    async def inline(fn, *a, **kw):
        return fn(*a, **kw)
    monkeypatch.setattr(asyncio, "to_thread", inline)

    async def no_cover(*_a, **_k):
        return [], []
    monkeypatch.setattr(service, "_cover_cells", no_cover)


def way(wid, coords, **tags):
    return importer.normalize_osm_way(wid, {"highway": "path", **tags}, coords)


# ---------- which ways, and how they're described ----------

@pytest.mark.parametrize("tags,kind", [
    ({"highway": "path"}, "foot"), ({"highway": "footway"}, "foot"), ({"highway": "bridleway"}, "horse"),
    ({"highway": "path", "horse": "designated"}, "horse"), ({"highway": "track"}, "track"),
    ({"highway": "footway", "footway": "sidewalk"}, None), ({"highway": "footway", "footway": "crossing"}, None),
    ({"highway": "path", "area": "yes"}, None), ({"highway": "residential"}, None),
])
def test_classify(tags, kind):
    assert importer.classify(tags) == kind


def test_features_keep_name_access_surface_or_get_a_placeholder():
    f = way(7, [[0, 0], [1, 1]], name=" Natural Bridge Trail ", access="permissive", surface="dirt")
    assert f["id"] == "osm-7" and f["properties"] == {"name": "Natural Bridge Trail", "kind": "foot",
                                                      "access": "permissive", "surface": "dirt",
                                                      "source": "OpenStreetMap"}
    t = importer.normalize_osm_way(8, {"highway": "track"}, [[0, 0], [1, 1]])
    assert t["properties"]["name"] == "Unnamed track" and t["properties"]["access"] is None


def test_a_line_on_a_cell_edge_is_stored_in_both_cells():
    cells = importer.cells_touched([[-88.76, 37.6], [-88.74, 37.6]])   # crosses -88.75, a 0.25° line
    assert len(cells) == 2


def test_reading_an_osm_file(tmp_path, monkeypatch):
    monkeypatch.setattr(importer, "WORK_DIR", str(tmp_path))
    osm = tmp_path / "tiny.osm"
    osm.write_text("""<?xml version='1.0' encoding='UTF-8'?>
<osm version="0.6">
  <node id="1" lat="37.52" lon="-88.66" version="1"/><node id="2" lat="37.521" lon="-88.655" version="1"/>
  <node id="3" lat="37.522" lon="-88.65" version="1"/><node id="4" lat="37.53" lon="-88.66" version="1"/>
  <way id="10" version="1"><nd ref="1"/><nd ref="2"/><nd ref="3"/>
    <tag k="highway" v="path"/><tag k="name" v="Natural Bridge Trail"/></way>
  <way id="11" version="1"><nd ref="1"/><nd ref="4"/><tag k="highway" v="footway"/><tag k="footway" v="sidewalk"/></way>
  <way id="12" version="1"><nd ref="1"/><nd ref="4"/><tag k="highway" v="residential"/></way>
  <way id="13" version="1"><nd ref="4"/><nd ref="99"/><tag k="highway" v="track"/></way>
</osm>""")
    cells, kept = importer.parse_extract(str(osm))
    feats = [json.loads(t) for texts in cells.values() for t in texts]
    assert kept == 1 and [f["id"] for f in feats] == ["osm-10"]       # sidewalk, road, broken way skipped
    assert feats[0]["properties"]["name"] == "Natural Bridge Trail"


# ---------- which states ----------

def index_entry(eid, geom):
    return {"properties": {"id": eid, "urls": {"pbf": f"https://example/{eid}.osm.pbf"}}, "geometry": geom}


def test_the_smallest_us_extract_containing_the_point_is_picked():
    small = {"type": "Polygon", "coordinates": [[[-89, 37.2], [-88.2, 37.2], [-88.2, 37.8], [-89, 37.8], [-89, 37.2]]]}
    index = [index_entry("north-america", STATE), index_entry("us/illinois", STATE), index_entry("us/part", small)]
    assert importer.pick_extract(index, 37.5, -88.66)["properties"]["id"] == "us/part"
    assert importer.pick_extract(index, 40.0, -80.0) is None


# ---------- the job ----------

class Progress:
    def __init__(self):
        self.failed = []

    def set_total(self, n): pass
    def begin(self, item): pass
    def step(self): pass

    def fail(self, item, error):
        self.failed.append(item)


def _add_region(lat=37.51, lon=-88.66):
    with Session(engine) as s:
        s.add(Region(name="R", lat=lat, lon=lon, created_at="x"))
        s.commit()


def test_job_imports_needed_states_and_skips_fresh_ones(db, monkeypatch):
    _add_region()

    async def index():
        return [index_entry("us/illinois", STATE)]
    calls = []

    async def fake_import(eid, url):
        calls.append(eid)
        importer.store_cells(eid, {(365, 510): [json.dumps(way(1, [[-88.66, 37.52], [-88.65, 37.52]]))]}, 1)
        return 1
    monkeypatch.setattr(importer, "fetch_index", index)
    monkeypatch.setattr(importer, "import_extract", fake_import)
    assert "illinois" in asyncio.run(importer.osm_trails_job(Progress()))
    assert asyncio.run(importer.osm_trails_job(Progress())) == "up to date" and calls == ["us/illinois"]


def test_import_extract_downloads_imports_and_cleans_up(db, monkeypatch, tmp_path):
    """The real import_extract, with only the download and the subprocess stubbed: it must report the subprocess's
    count, refresh the served data and delete the downloaded file (a NameError here once failed every import)."""
    monkeypatch.setattr(importer, "WORK_DIR", str(tmp_path))
    with Session(engine) as s:
        s.add(OsmExtract(extract_id="us/arkansas", pbf_url="u", polygon_json=json.dumps(STATE), status="queued"))
        s.commit()

    async def fake_download(url, dest):
        with open(dest, "wb") as fh:
            fh.write(b"x" * 1000)

    async def fake_subprocess(eid, pbf):
        importer.store_cells(eid, {(1, 1): ["{}"]}, 42)
        return 42
    invalidated = []
    monkeypatch.setattr(importer, "_download", fake_download)
    monkeypatch.setattr(importer, "_import_in_subprocess", fake_subprocess)
    monkeypatch.setattr(service, "invalidate", lambda: invalidated.append(1))
    assert asyncio.run(importer.import_extract("us/arkansas", "u")) == 42
    assert invalidated and not list(tmp_path.iterdir())
    with Session(engine) as s:
        assert s.get(OsmExtract, "us/arkansas").status == "ok"


def test_a_failed_reimport_keeps_the_previous_cells(db, monkeypatch):
    _add_region()

    async def index():
        return [index_entry("us/illinois", STATE)]

    async def broken(eid, url):
        raise RuntimeError("download failed")
    monkeypatch.setattr(importer, "fetch_index", index)
    monkeypatch.setattr(importer, "import_extract", broken)
    with Session(engine) as s:
        s.add(OsmExtract(extract_id="us/illinois", pbf_url="u", polygon_json=json.dumps(STATE), status="queued"))
        s.commit()
    importer.store_cells("us/illinois", {(1, 1): ["{}"]}, 1)
    with Session(engine) as s:
        s.get(OsmExtract, "us/illinois").imported_at = 0   # a month+ old: due again
        s.commit()
    p = Progress()
    asyncio.run(importer.osm_trails_job(p))
    with Session(engine) as s:
        row = s.get(OsmExtract, "us/illinois")
        assert p.failed == ["us/illinois"] and row.status == "failed" and "download failed" in row.error
        assert s.scalars(select(OsmTrailCell)).first() is not None


def test_states_no_region_needs_are_dropped(db, monkeypatch):
    async def index():
        return [index_entry("us/illinois", STATE)]
    monkeypatch.setattr(importer, "fetch_index", index)
    with Session(engine) as s:
        s.add(OsmExtract(extract_id="us/illinois", pbf_url="u", polygon_json=json.dumps(STATE), status="ok"))
        s.commit()
    importer.store_cells("us/illinois", {(1, 1): ["{}"]}, 1)
    asyncio.run(importer.osm_trails_job(Progress()))                      # no regions at all
    with Session(engine) as s:
        assert s.scalars(select(OsmExtract)).first() is None and s.scalars(select(OsmTrailCell)).first() is None


# ---------- serving ----------

TRAIL = [[-88.66, 37.52], [-88.65, 37.52]]
VIEW = dict(south=37.51, west=-88.67, north=37.53, east=-88.64, zoom=14)


def _imported(features, status="ok"):
    with Session(engine) as s:
        s.add(OsmExtract(extract_id="us/illinois", pbf_url="u", polygon_json=json.dumps(STATE), status=status,
                         imported_at=1.0 if status == "ok" else None))
        s.commit()
    if features:
        cells = {}
        for f in features:
            for c in importer.cells_touched(f["geometry"]["coordinates"]):
                cells.setdefault(c, []).append(json.dumps(f))
        importer.store_cells("us/illinois", cells, len(features))
    service.invalidate()


def test_trails_in_view_are_served(db):
    _imported([way(1, TRAIL, name="Natural Bridge Trail"), way(2, [[-88.0, 37.9], [-87.99, 37.9]])])
    out = asyncio.run(service.osm_trails(**VIEW))
    assert out["status"] == "ok" and "OpenStreetMap" in out["attribution"]
    assert [f["properties"]["name"] for f in out["features"]] == ["Natural Bridge Trail"]   # far one left out


def test_a_trail_the_forest_service_already_draws_is_cut(db, monkeypatch):
    _imported([way(1, TRAIL)])
    fs = [{"type": "Feature", "id": "trail-1", "properties": {"kind": "trail"},
           "geometry": {"type": "LineString", "coordinates": [[-88.6601, 37.52005], [-88.6499, 37.52005]]}}]

    async def cover(*_a, **_k):
        return [fs], []
    monkeypatch.setattr(service, "_cover_cells", cover)
    assert asyncio.run(service.osm_trails(**VIEW))["features"] == []


def test_a_track_under_a_usgs_road_is_cut_but_a_foot_trail_is_not(db, monkeypatch):
    _imported([importer.normalize_osm_way(1, {"highway": "track"}, TRAIL), way(2, TRAIL, name="Foot")])
    road = [{"type": "Feature", "id": "local-1", "properties": {"kind": "local"},
             "geometry": {"type": "LineString", "coordinates": [[-88.6601, 37.52005], [-88.6499, 37.52005]]}}]

    async def cover(*_a, **_k):
        return [], [road]
    monkeypatch.setattr(service, "_cover_cells", cover)
    assert [f["properties"]["name"] for f in asyncio.run(service.osm_trails(**VIEW))["features"]] == ["Foot"]


def test_status_tells_importing_and_not_downloaded_apart(db):
    _imported([], status="importing")
    assert asyncio.run(service.osm_trails(**VIEW))["status"] == "importing"
    assert asyncio.run(service.osm_trails(south=45.0, west=-100.0, north=45.02, east=-99.97, zoom=14))["status"] == "none"


def test_zoomed_out_and_huge_views_are_rejected(db):
    with pytest.raises(service.BadBounds):
        asyncio.run(service.osm_trails(**{**VIEW, "zoom": 10}))
    with pytest.raises(service.BadBounds):
        asyncio.run(service.osm_trails(south=36.0, west=-90.0, north=38.0, east=-88.0, zoom=11))


def test_zoomed_out_views_are_simplified(db):
    wiggly = [[-88.66 + i * 0.0001, 37.52 + (0.00001 if i % 2 else 0)] for i in range(100)]
    _imported([way(1, wiggly)])
    far = asyncio.run(service.osm_trails(**{**VIEW, "zoom": 11}))["features"][0]["geometry"]["coordinates"]
    near = asyncio.run(service.osm_trails(**VIEW))["features"][0]["geometry"]["coordinates"]
    assert len(far) < len(near) == 100


def test_reading_a_file_whose_nodes_are_not_in_id_order(tmp_path):
    osm = tmp_path / "unsorted.osm"
    osm.write_text("""<?xml version='1.0' encoding='UTF-8'?>
<osm version="0.6">
  <node id="30" lat="37.522" lon="-88.65" version="1"/><node id="10" lat="37.52" lon="-88.66" version="1"/>
  <node id="20" lat="37.521" lon="-88.655" version="1"/>
  <way id="1" version="1"><nd ref="10"/><nd ref="20"/><nd ref="30"/><tag k="highway" v="track"/></way>
</osm>""")
    cells, kept = importer.parse_extract(str(osm))
    [text] = [t for texts in cells.values() for t in texts]
    assert kept == 1 and json.loads(text)["geometry"]["coordinates"][0] == [-88.66, 37.52]


def test_the_cell_cache_is_bounded(db, monkeypatch):
    monkeypatch.setattr(service, "MAX_CACHED_CELLS", 3)
    service._load_cells([f"osm:x:{i}:0" for i in range(5)])
    assert list(service._cells) == [f"osm:x:{i}:0" for i in (2, 3, 4)]
