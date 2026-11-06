import asyncio
import json

import httpx
import pytest
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

import app.main  # noqa: F401  (registers every model so create_all sees all tables)
from app.core.database import Base, engine
from app.osmtrails import importer
from app.osmtrails import service as osm_service
from app.osmtrails.models import OsmExtract, OsmTrailCell, OsmTrailName
from app.search import service

LAT, LON = 36.0, -93.2


@pytest.fixture(autouse=True)
def _fresh():
    service.clear_cache()
    service._backfill["started"] = True   # tests start the backfill themselves
    yield
    service.clear_cache()


@pytest.fixture()
def db(monkeypatch):
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        for model in (OsmTrailName, OsmTrailCell, OsmExtract):
            s.execute(delete(model))
        s.commit()
    osm_service.invalidate()

    async def inline(fn, *a, **kw):
        return fn(*a, **kw)
    monkeypatch.setattr(asyncio, "to_thread", inline)


def r(name, lat, lon, source="esri", kind="water", **kw):
    return {"name": name, "lat": lat, "lon": lon, "source": source, "kind": kind, "sub": "", **kw}


# ---------- query text ----------

def test_query_is_cleaned_of_wildcards_and_quotes_are_doubled():
    assert service.clean_query("  O'Brien_%  Creek; DROP ") == "O'Brien Creek DROP"
    assert service.sql_like_text(service.clean_query("O'Brien")) == "O''BRIEN"


@pytest.mark.parametrize("q,route", [("FS Rd 1003A", "1003A"), ("FR 1003", "1003"), ("1003", "1003"),
                                     ("Forest Service Road 1440", "1440"), ("Bear Creek", None), ("FS Rd", None)])
def test_route_number(q, route):
    assert service.route_number(q) == route


@pytest.mark.parametrize("name,rank", [("Bear Creek", 0), ("bear creek rd", 1), ("Little Bear Creek", 2),
                                       ("Teddybear Creek", 3)])
def test_match_rank(name, rank):
    assert service.match_rank(name, "Bear Creek") == rank


# ---------- ranking ----------

def test_a_close_partial_match_beats_an_exact_one_across_the_country():
    out = service.rank_and_dedupe([r("Bear Creek", 45.0, -110.0), r("Bear Creek Rd", 36.02, -93.2, kind="street")],
                                  "Bear Creek", LAT, LON)
    assert [x["name"] for x in out] == ["Bear Creek Rd", "Bear Creek"]
    assert out[0]["distance_mi"] == pytest.approx(1.4, abs=0.1)


def test_same_name_nearby_is_shown_once_preferring_the_drawable_line():
    out = service.rank_and_dedupe([r("Bear Creek", 36.01, -93.2), r("BEAR CREEK", 36.012, -93.2, source="mvum",
                                                                     kind="trail", geometry={})],
                                  "Bear Creek", LAT, LON)
    assert len(out) == 1 and out[0]["source"] == "mvum"
    far = service.rank_and_dedupe([r("Bear Creek", 36.01, -93.2), r("Bear Creek", 36.5, -93.2)], "Bear Creek", LAT, LON)
    assert len(far) == 2


def test_results_are_capped():
    many = [r(f"Bear Creek {i}", LAT + i / 100, LON) for i in range(40)]
    assert len(service.rank_and_dedupe(many, "Bear Creek", LAT, LON)) == service.MAX_RESULTS


# ---------- sources ----------

def test_one_failing_source_still_returns_the_others(monkeypatch):
    async def ok(_c, q, lat, lon):
        return [r("Bear Creek", 36.01, -93.2)]

    async def boom(*_a):
        raise httpx.ConnectError("down")
    monkeypatch.setattr(service, "search_esri", ok)
    monkeypatch.setattr(service, "search_gnis", boom)
    monkeypatch.setattr(service, "search_mvum", boom)
    monkeypatch.setattr(service, "search_osm", lambda *a: [])
    out = asyncio.run(service.search("Bear Creek", LAT, LON))
    assert [x["name"] for x in out["results"]] == ["Bear Creek"]
    assert set(out["failed"]) == {"place names", "Forest Service trails"}
    assert out["results"][0]["id"]


def test_too_short_a_query_is_refused():
    with pytest.raises(service.BadQuery):
        asyncio.run(service.search(" a%", LAT, LON))


def test_gnis_searches_the_whole_country_only_when_nothing_matches_nearby(monkeypatch):
    calls = []

    async def layer(_c, layer, q, box):
        calls.append((layer, box is not None))
        if box is None and layer == 6:
            return [r("Zebra Creek", 45.0, -110.0, source="gnis")]
        return []
    monkeypatch.setattr(service, "_gnis_layer", layer)
    out = asyncio.run(service.search_gnis(None, "Zebra Creek", LAT, LON))
    assert [x["name"] for x in out] == ["Zebra Creek"]
    assert sorted(calls) == sorted([(lay, True) for lay in service.GNIS_LAYERS] +
                                   [(lay, False) for lay in service.GNIS_LAYERS])
    calls.clear()
    asyncio.run(service.search_gnis(None, "Zebra Creek", LAT, LON))
    assert calls == []   # cached


def test_gnis_where_clause_is_quoted(monkeypatch):
    seen = {}

    def handler(request):
        seen["where"] = request.url.params["where"]
        return httpx.Response(200, json={"type": "FeatureCollection", "features": [{
            "type": "Feature", "geometry": {"type": "MultiPoint", "coordinates": [[-93.1, 36.1]]},
            "properties": {"gaz_name": "O'Brien Creek", "gaz_featureclass": "Stream", "county_name": "Newton",
                           "state_alpha": "AR"}}]})

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
            return await service._gnis_layer(c, 6, "O'Brien", (1, 2, 3, 4))
    out = asyncio.run(go())
    assert seen["where"] == "UPPER(gaz_name) LIKE '%O''BRIEN%'"
    assert out == [{"kind": "water", "name": "O'Brien Creek", "sub": "Stream (mouth) · Newton Co., AR",
                    "lat": 36.1, "lon": -93.1, "source": "gnis"}]


def test_esri_candidates_are_typed_and_weak_guesses_dropped():
    def cand(address, score, addr_type, typ="", **extra):
        return {"address": address, "score": score, "location": {"x": -93.1, "y": 36.0},
                "attributes": {"Addr_type": addr_type, "Type": typ, "Subregion": "Newton County",
                               "Region": "Arkansas"}, **extra}

    def handler(_request):
        return httpx.Response(200, json={"candidates": [
            cand("Jasper, Arkansas", 100, "Locality", "City",
                 extent={"xmin": -93.2, "ymin": 35.9, "xmax": -93.1, "ymax": 36.1}),
            cand("Cave Mount Rd, Pettigrew, Arkansas, 72752", 100, "StreetName"),
            cand("Buffalo River", 100, "POI", "Stream"),
            cand("Buffalo River Trail", 99.95, "POI", "Trail"),
            cand("Hollow, Oklahoma", 78.46, "Locality", "City"),
        ]})

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
            return await service.search_esri(c, "x", LAT, LON)
    out = asyncio.run(go())
    assert [(x["kind"], x["name"], x["sub"]) for x in out] == [
        ("town", "Jasper", "Newton County, Arkansas"),
        ("street", "Cave Mount Rd", "Pettigrew, Arkansas"),
        ("water", "Buffalo River", "Stream · Newton County, Arkansas"),
        ("trail", "Buffalo River Trail", "Trail · Newton County, Arkansas"),
    ]
    assert out[0]["bbox"] == [-93.2, 35.9, -93.1, 36.1] and "bbox" not in out[2]


def test_mvum_lines_are_merged_and_unnamed_roads_go_by_number(monkeypatch):
    def handler(request):
        assert "OR UPPER(id) LIKE '1003%'" in request.url.params["where"]
        if "/2/" in request.url.path:
            return httpx.Response(200, json={"type": "FeatureCollection", "features": []})
        seg = lambda oid, a, b: {"type": "Feature", "id": oid, "geometry": {"type": "LineString", "coordinates": [a, b]},
                                 "properties": {"name": None, "id": "1003", "passengervehicle": "open",
                                                "forestname": "Ozark"}}
        return httpx.Response(200, json={"type": "FeatureCollection", "features": [
            seg(1, [-93.0, 36.0], [-93.01, 36.01]), seg(2, [-93.01, 36.01], [-93.02, 36.02])]})

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
            return await service.search_mvum(c, "FS Rd 1003", LAT, LON)
    out = asyncio.run(go())
    assert len(out) == 1
    assert out[0]["name"] == "FS Rd 1003" and out[0]["trail_kind"] == "road"
    assert out[0]["geometry"]["type"] in ("LineString", "MultiLineString")
    assert out[0]["bbox"] == [-93.02, 36.0, -93.0, 36.02]


# ---------- OpenStreetMap name index ----------

def osm_feature(fid, coords, name, kind="foot"):
    return {"type": "Feature", "id": fid, "geometry": {"type": "LineString", "coordinates": coords},
            "properties": {"name": name, "kind": kind, "access": None, "surface": None, "source": "OpenStreetMap"}}


def store(extract_id, cells):
    with Session(engine) as s:
        s.add(OsmExtract(extract_id=extract_id, pbf_url="x", polygon_json="{}", status="ok", way_count=1))
        for key, feats in cells.items():
            s.add(OsmTrailCell(key=key, extract_id=extract_id, payload=json.dumps(feats)))
        s.commit()


def test_name_index_groups_stretches_and_skips_unnamed(db):
    a1 = osm_feature("osm-1", [[-93.0, 36.0], [-93.01, 36.0]], "Bear Creek Trail")
    a2 = osm_feature("osm-2", [[-93.01, 36.0], [-93.02, 36.0]], "Bear Creek Trail")
    far = osm_feature("osm-3", [[-92.0, 36.5], [-92.01, 36.5]], "Bear Creek Trail")
    nameless = osm_feature("osm-4", [[-93.0, 36.0], [-93.01, 36.01]], "Unnamed trail")
    # a1 sits on a cell edge, so it's stored in two cells: indexed once, with both cells
    store("us/arkansas", {"osm:us/arkansas:1:1": [a1, a2, nameless], "osm:us/arkansas:1:2": [a1],
                          "osm:us/arkansas:5:5": [far]})
    assert importer.build_name_index("us/arkansas") == 2
    rows = service.search_osm("bear creek", LAT, LON)
    assert sorted(len(json.loads(r.feature_ids)) for r in _rows()) == [1, 2]
    near = min(rows, key=lambda x: abs(x["lon"] - -93.01))
    assert near["trail_kind"] == "foot" and near["bbox"] == [-93.02, 36.0, -93.0, 36.0]

    line = service.osm_trail_line(near["ref"])
    assert line["geometry"]["type"] == "MultiLineString" and len(line["geometry"]["coordinates"]) == 2
    assert service.osm_trail_line(999999) is None
    assert service.search_osm("100%", LAT, LON) == []   # LIKE wildcards are literal


def _rows():
    with Session(engine) as s:
        return s.scalars(select(OsmTrailName)).all()


def test_backfill_indexes_only_states_without_an_index(db):
    store("us/arkansas", {"osm:us/arkansas:1:1": [osm_feature("osm-1", [[-93.0, 36.0], [-93.01, 36.0]], "Ridge Trail")]})
    assert importer.backfill_name_index() == ["us/arkansas"]
    assert importer.backfill_name_index() == []
    assert [r.name for r in _rows()] == ["Ridge Trail"]


def test_a_very_common_name_is_grouped_by_area_not_pairwise():
    pieces = [{"bbox": (i * 0.001, 0, i * 0.001, 0)} for i in range(importer.MAX_PAIRWISE + 1)]
    groups = importer.cluster_pieces(pieces)
    assert sum(len(g) for g in groups) == len(pieces) and len(groups) == 5   # 0.4° wide: five 0.1° squares
