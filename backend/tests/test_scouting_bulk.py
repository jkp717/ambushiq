import pytest
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

import app.main  # noqa: F401  (registers every model so create_all sees all tables)
from app.core.database import Base, engine
from app.scouting import router as scout_router
from app.scouting.models import ScoutingSuggestion
from app.scouting.schemas import ScoutingBulkIn


def _row(region_id, score=50.0, status="new"):
    return ScoutingSuggestion(region_id=region_id, lat=34.7, lon=-92.3, radius_m=60.0, score=score,
                              reasoning_json="{}", status=status, request_lat=34.7, request_lon=-92.3,
                              request_radius_m=500.0, created_at="")


@pytest.fixture()
def ids():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        rows = [_row(1), _row(1), _row(1, status="dismissed"), _row(2)]
        s.add_all(rows)
        s.commit()
        return [r.id for r in rows]


def _call(region_id, id_list, action):
    return scout_router.bulk_update(ScoutingBulkIn(ids=id_list, action=action), region_id=region_id, _=None)


def _state():
    with Session(engine) as s:
        return {r.id: (r.region_id, r.status) for r in s.scalars(select(ScoutingSuggestion)).all()}


def test_bulk_delete_removes_only_requested_rows_in_the_active_region(ids):
    a, b, c, other = ids
    assert _call(1, [a, b], "delete") == {"ok": True, "affected": 2}
    assert set(_state()) == {c, other}


def test_ids_from_another_region_and_unknown_ids_are_ignored(ids):
    a, b, c, other = ids
    assert _call(1, [other, 99999], "delete")["affected"] == 0
    assert other in _state()
    assert _call(1, [other, a], "dismiss")["affected"] == 1
    assert _state()[other] == (2, "new")


def test_dismiss_and_restore_set_status(ids):
    a, b, c, other = ids
    assert _call(1, [a, b], "dismiss")["affected"] == 2
    assert _state()[a] == (1, "dismissed") and _state()[b] == (1, "dismissed")
    assert _call(1, [a, c], "restore")["affected"] == 2
    assert _state()[a] == (1, "new") and _state()[c] == (1, "new") and _state()[b] == (1, "dismissed")


def test_empty_selection_is_a_no_op(ids):
    assert _call(1, [], "delete") == {"ok": True, "affected": 0}
    assert len(_state()) == 4


def test_schema_rejects_unknown_actions_and_oversized_selections():
    with pytest.raises(ValidationError):
        ScoutingBulkIn(ids=[1], action="explode")
    with pytest.raises(ValidationError):
        ScoutingBulkIn(ids=list(range(5001)), action="delete")
