import pytest
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

import app.main  # noqa: F401  (registers every model so create_all sees all tables)
from app.bulk import router as bulk_router
from app.bulk.schemas import BulkIn
from app.cameras.models import Camera
from app.core.database import Base, engine
from app.corridors.models import Corridor
from app.deer_sign.models import DeerSign
from app.scouting.models import ScoutingSuggestion
from app.stands.models import Stand
from app.zones.models import Zone


@pytest.fixture()
def rows():
    """Two rows of every kind in region 1, plus one of each in region 2 (never touchable from 1)."""
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    ids = {}
    with Session(engine) as s:
        def add(kind, obj):
            s.add(obj)
            s.flush()
            ids.setdefault(kind, []).append(obj.id)

        for region in (1, 1, 2):
            add("stand", Stand(region_id=region, name="s", lat=34.7, lon=-92.3, is_active=1))
            add("zone", Zone(region_id=region, kind="food", name="z", lat=34.7, lon=-92.3, radius_m=50, is_active=1))
            add("corridor", Corridor(region_id=region, name="c", points_json="[]", is_active=1))
            add("sign", DeerSign(region_id=region, kind="rub", name="r", lat=34.7, lon=-92.3, is_active=1, created_at=""))
            add("suggestion", ScoutingSuggestion(region_id=region, lat=34.7, lon=-92.3, radius_m=60.0, score=50.0,
                                                 reasoning_json="{}", status="new", request_lat=34.7,
                                                 request_lon=-92.3, request_radius_m=500.0, created_at=""))
        s.commit()
    return ids


def _call(items, action, region_id=1):
    return bulk_router.bulk_update(BulkIn(items=items, action=action), region_id=region_id, _=None)


def _items(rows, *picks):
    """picks like ("zone", 0) -> {"kind": "zone", "id": rows["zone"][0]}"""
    return [{"kind": kind, "id": rows[kind][idx]} for kind, idx in picks]


def _flag(model, id_):
    with Session(engine) as s:
        row = s.get(model, id_)
        return None if row is None else (row.status if isinstance(row, ScoutingSuggestion) else row.is_active)


def test_deactivate_and_activate_map_to_is_active_or_scouting_status(rows):
    picks = _items(rows, ("stand", 0), ("zone", 0), ("corridor", 0), ("sign", 0), ("suggestion", 0))
    out = _call(picks, "deactivate")
    assert out["affected"] == {"stand": 1, "zone": 1, "corridor": 1, "sign": 1, "suggestion": 1}
    assert [_flag(m, rows[k][0]) for k, m in (("stand", Stand), ("zone", Zone), ("corridor", Corridor), ("sign", DeerSign))] == [0, 0, 0, 0]
    assert _flag(ScoutingSuggestion, rows["suggestion"][0]) == "dismissed"
    assert _flag(Zone, rows["zone"][1]) == 1                    # untouched sibling

    _call(picks, "activate")
    assert _flag(Stand, rows["stand"][0]) == 1
    assert _flag(ScoutingSuggestion, rows["suggestion"][0]) == "new"


def test_mixed_selection_of_scouting_and_food_zone(rows):
    out = _call(_items(rows, ("suggestion", 1), ("zone", 1)), "deactivate")
    assert out["affected"]["suggestion"] == 1 and out["affected"]["zone"] == 1
    assert _flag(ScoutingSuggestion, rows["suggestion"][1]) == "dismissed"
    assert _flag(Zone, rows["zone"][1]) == 0


def test_delete_removes_only_requested_rows(rows):
    out = _call(_items(rows, ("zone", 0), ("suggestion", 0), ("sign", 1)), "delete")
    assert out["affected"] == {"stand": 0, "zone": 1, "corridor": 0, "sign": 1, "suggestion": 1}
    assert _flag(Zone, rows["zone"][0]) is None and _flag(Zone, rows["zone"][1]) == 1
    assert _flag(DeerSign, rows["sign"][1]) is None


def test_other_regions_and_unknown_ids_are_ignored(rows):
    foreign = _items(rows, ("stand", 2), ("zone", 2), ("suggestion", 2)) + [{"kind": "sign", "id": 99999}]
    assert sum(_call(foreign, "delete")["affected"].values()) == 0
    assert _flag(Stand, rows["stand"][2]) == 1 and _flag(Zone, rows["zone"][2]) == 1
    assert sum(_call(foreign, "deactivate")["affected"].values()) == 0
    assert _flag(ScoutingSuggestion, rows["suggestion"][2]) == "new"


def test_deleting_stands_unassigns_their_cameras(rows):
    with Session(engine) as s:
        s.add_all([Camera(region_id=1, name="a", brand="x", stand_id=rows["stand"][0], is_active=1),
                   Camera(region_id=1, name="b", brand="x", stand_id=rows["stand"][1], is_active=1)])
        s.commit()
    out = _call(_items(rows, ("stand", 0)), "delete")
    assert out["affected"]["stand"] == 1 and out["cameras_unassigned"] == 1
    with Session(engine) as s:
        assigned = {c.name: c.stand_id for c in s.scalars(select(Camera)).all()}
    assert assigned == {"a": None, "b": rows["stand"][1]}


def test_empty_selection_is_a_no_op(rows):
    assert _call([], "delete") == {"ok": True, "affected": {k: 0 for k in rows}, "cameras_unassigned": 0}
    assert _flag(Stand, rows["stand"][0]) == 1


def test_schema_rejects_unknown_kinds_actions_and_oversized_selections():
    with pytest.raises(ValidationError):
        BulkIn(items=[{"kind": "camera", "id": 1}], action="delete")
    with pytest.raises(ValidationError):
        BulkIn(items=[{"kind": "zone", "id": 1}], action="explode")
    with pytest.raises(ValidationError):
        BulkIn(items=[{"kind": "zone", "id": i} for i in range(5001)], action="delete")
