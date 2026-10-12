import os
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

import app.main  # noqa: F401  (registers every model so create_all sees all tables)
from app import scheduler
from app.cameras import router as cam_router
from app.cameras.models import Camera, CameraSighting
from app.cameras.schemas import FavoriteIn
from app.core.database import Base, engine
from app.regions import router as region_router
from app.regions.models import Region

NOW = datetime.now(timezone.utc).replace(microsecond=0)
OLD = NOW - timedelta(days=90)           # past the 60-day retention


def iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


@pytest.fixture()
def env(monkeypatch, tmp_path):
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    monkeypatch.setattr(scheduler, "get_settings", lambda: {"image_retention_days": 60})
    monkeypatch.setattr(cam_router, "get_camera_dir", lambda *a, **k: str(tmp_path))
    with Session(engine) as s:
        s.add(Region(id=1, name="r", lat=34.7, lon=-92.3, property_timezone="America/Chicago"))
        cam = Camera(region_id=1, name="North", brand="spypoint", provider_ref="cam-A", is_active=1,
                     is_deleted=0, created_at=iso(NOW))
        s.add(cam)
        s.commit()
        cid = cam.id

    def add(name, *, favorite=False, when=OLD):
        path = tmp_path / f"{name}.jpg"
        path.write_bytes(b"jpg")
        with Session(engine) as s:
            sg = CameraSighting(camera_id=cid, timestamp=iso(when), created_at=iso(when), confidence_score=0.9,
                                image_path=str(path), is_animal=1, is_favorite=1 if favorite else 0)
            s.add(sg)
            s.commit()
            return sg.id, str(path)

    return {"cid": cid, "add": add}


def sighting(sid):
    with Session(engine) as s:
        return s.get(CameraSighting, sid)


def gallery(**kw):
    args = dict(brand=[], camera_id=[], species=[], date_from=None, date_to=None, hour_from=None, hour_to=None,
                include_empty=False, favorites=False, before_ts=None, before_id=None, limit=48, region_id=1, _=None)
    return cam_router.camera_gallery(**{**args, **kw})["items"]


def test_cleanup_keeps_favorites_and_removes_the_rest(env):
    fav_id, fav_path = env["add"]("fav", favorite=True)
    old_id, old_path = env["add"]("old")
    scheduler.auto_cleanup_job(None)
    assert sighting(fav_id).image_path == fav_path and os.path.exists(fav_path)
    assert sighting(old_id).image_path is None and not os.path.exists(old_path)


def test_favorite_endpoint_sets_and_clears(env):
    sid, _ = env["add"]("a", when=NOW)
    assert cam_router.set_sighting_favorite(sid, FavoriteIn(favorite=True), region_id=1, _=None)["is_favorite"] is True
    assert cam_router.set_sighting_favorite(sid, FavoriteIn(favorite=False), region_id=1, _=None)["is_favorite"] is False


def test_favorite_endpoint_is_scoped_to_the_region(env):
    sid, _ = env["add"]("a", when=NOW)
    with pytest.raises(Exception):
        cam_router.set_sighting_favorite(sid, FavoriteIn(favorite=True), region_id=2, _=None)


def test_deleting_a_camera_keeps_its_favorites_in_the_gallery(env):
    fav_id, fav_path = env["add"]("fav", favorite=True, when=NOW)
    other_id, other_path = env["add"]("other", when=NOW)
    cam_router.delete_camera(env["cid"], delete_images=True, region_id=1, _=None)
    assert sighting(other_id) is None and not os.path.exists(other_path)
    assert sighting(fav_id) is not None and os.path.exists(fav_path)
    assert [i["id"] for i in gallery()] == [fav_id]


def test_favorites_filter(env):
    fav_id, _ = env["add"]("fav", favorite=True, when=NOW)
    env["add"]("other", when=NOW)
    assert [i["id"] for i in gallery(favorites=True)] == [fav_id]
    assert len(gallery()) == 2


def test_region_favorite_count(env):
    env["add"]("fav", favorite=True, when=NOW)
    env["add"]("other", when=NOW)
    assert region_router.region_favorite_count(1, _=None) == {"count": 1}
    assert region_router.region_favorite_count(2, _=None) == {"count": 0}
