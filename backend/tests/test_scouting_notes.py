import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy.orm import Session

import app.main  # noqa: F401  (registers every model so create_all sees all tables)
from app.bulk.schemas import BulkIn
from app.bulk import router as bulk_router
from app.core.database import Base, engine
from app.scouting import router as sr
from app.scouting.models import ScoutingSuggestion
from app.scouting.schemas import ScoutingColorIn, ScoutingCommentIn


@pytest.fixture()
def spot():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        row = ScoutingSuggestion(region_id=1, lat=34.7, lon=-92.3, radius_m=60.0, score=50.0, reasoning_json="{}",
                                 status="new", request_lat=34.7, request_lon=-92.3, request_radius_m=500.0,
                                 created_at="")
        s.add(row)
        s.commit()
        return row.id


def add(spot_id, text, region_id=1):
    return sr.add_comment(spot_id, ScoutingCommentIn(text=text), region_id=region_id, _=None)


def test_new_spots_have_no_color_and_no_comments(spot):
    out = sr.list_suggestions(region_id=1, _=None)[0]
    assert out["color"] is None and out["comments"] == []


def test_color_can_be_set_and_cleared(spot):
    out = sr.set_color(spot, ScoutingColorIn(color="#d32f2f"), region_id=1, _=None)
    assert out["color"] == "#D32F2F"
    assert sr.list_suggestions(region_id=1, _=None)[0]["color"] == "#D32F2F"
    assert sr.set_color(spot, ScoutingColorIn(color=None), region_id=1, _=None)["color"] is None


@pytest.mark.parametrize("bad", ["red", "#12345", "#GGGGGG", "javascript:alert(1)", "#1234567", ""])
def test_only_hex_colors_are_accepted(bad):
    with pytest.raises(ValidationError):
        ScoutingColorIn(color=bad)


def test_comments_are_added_in_order_with_id_and_timestamp(spot):
    add(spot, "  first note  ")
    out = add(spot, "second note")
    assert [c["text"] for c in out["comments"]] == ["first note", "second note"]
    assert all(c["id"] and c["created_at"] for c in out["comments"])
    assert out["comments"][0]["id"] != out["comments"][1]["id"]
    assert len(sr.list_suggestions(region_id=1, _=None)[0]["comments"]) == 2


@pytest.mark.parametrize("bad", ["", "   ", "x" * 2001])
def test_blank_or_oversized_comments_are_rejected(bad):
    with pytest.raises(ValidationError):
        ScoutingCommentIn(text=bad)


def test_a_comment_can_be_deleted_without_touching_the_others(spot):
    add(spot, "keep")
    out = add(spot, "drop")
    drop_id = out["comments"][1]["id"]
    out = sr.delete_comment(spot, drop_id, region_id=1, _=None)
    assert [c["text"] for c in out["comments"]] == ["keep"]
    assert sr.delete_comment(spot, "nonexistent", region_id=1, _=None)["comments"] == out["comments"]


def test_other_regions_cannot_touch_a_spot(spot):
    for call in (
        lambda: sr.set_color(spot, ScoutingColorIn(color="#D32F2F"), region_id=2, _=None),
        lambda: add(spot, "hi", region_id=2),
        lambda: sr.delete_comment(spot, "x", region_id=2, _=None),
    ):
        with pytest.raises(HTTPException) as e:
            call()
        assert e.value.status_code == 404
    assert sr.list_suggestions(region_id=1, _=None)[0]["comments"] == []


def test_unknown_spot_is_a_404():
    Base.metadata.create_all(engine)
    with pytest.raises(HTTPException) as e:
        add(999999, "hi")
    assert e.value.status_code == 404


def test_corrupt_comment_data_reads_as_no_comments(spot):
    with Session(engine) as s:
        row = s.get(ScoutingSuggestion, spot)
        row.comments_json = "not json"
        s.commit()
    assert sr.list_suggestions(region_id=1, _=None)[0]["comments"] == []
    assert len(add(spot, "recovers")["comments"]) == 1


def test_status_changes_and_bulk_delete_still_work_with_notes(spot):
    add(spot, "note")
    out = sr.update_status(spot, sr.ScoutingStatusIn(status="dismissed"), region_id=1, _=None)
    assert out["status"] == "dismissed" and len(out["comments"]) == 1
    bulk_router.bulk_update(BulkIn(items=[{"kind": "suggestion", "id": spot}], action="delete"), region_id=1, _=None)
    assert sr.list_suggestions(region_id=1, _=None) == []
