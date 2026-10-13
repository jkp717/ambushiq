import json
from datetime import datetime, timedelta, timezone

import numpy as np
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

import app.main  # noqa: F401  (registers every model so create_all sees all tables)
from app.cameras import bucks, detection
from app.cameras import router as cam_router
from app.cameras.models import Camera, CameraSighting, DeerDetection
from app.core.database import Base, engine
from app.regions.models import Region

NOW = datetime.now(timezone.utc).replace(microsecond=0)
DIM = 16
rng = np.random.default_rng(1)


def iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def vec(buck: bool):
    v = rng.normal(0, 0.5, DIM)
    v[:4] += 2.0 if buck else -2.0          # two well-separated clusters
    return v


@pytest.fixture()
def env(monkeypatch, tmp_path):
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    monkeypatch.setattr(bucks, "_model_path", lambda: str(tmp_path / "buck_model.json"))
    bucks._model_cache.update(mtime=None, model=None)
    with Session(engine) as s:
        s.add(Region(id=1, name="r", lat=34.7, lon=-92.3, property_timezone="America/Chicago"))
        s.add(Region(id=2, name="other", lat=34.7, lon=-92.3, property_timezone="America/Chicago"))
        cam = Camera(region_id=1, name="North", brand="spypoint", provider_ref="cam-A", is_active=1,
                     is_deleted=0, created_at=iso(NOW))
        s.add(cam)
        s.commit()
        cid = cam.id
    n = {"i": 0}

    def add(buck_vec=None, *, label=None, with_file=True, when=None):
        """A photo with one deer; returns (sighting id, detection id)."""
        n["i"] += 1
        path = tmp_path / f"p{n['i']}.jpg"
        if with_file:
            from PIL import Image
            Image.new("RGB", (64, 48), (90, 120, 60)).save(path)
        with Session(engine) as s:
            sg = CameraSighting(camera_id=cid, timestamp=iso(when or NOW - timedelta(minutes=n["i"])),
                                created_at=iso(NOW), confidence_score=0.9, species="White-tailed Deer",
                                image_path=str(path), is_animal=1)
            s.add(sg)
            s.flush()
            d = DeerDetection(sighting_id=sg.id, box_json=json.dumps([0.1, 0.1, 0.6, 0.9]), det_conf=0.9,
                              species="White-tailed Deer",
                              vec=bucks.encode_vec(buck_vec if buck_vec is not None else vec(False)),
                              vec_source=detection.FEATURE_SOURCE, label=label)
            s.add(d)
            s.commit()
            return sg.id, d.id

    return {"cid": cid, "add": add}


def det(det_id):
    with Session(engine) as s:
        return s.get(DeerDetection, det_id)


def labeled_set(env, n_buck, n_not):
    out = {"bucks": [env["add"](vec(True), label="buck")[1] for _ in range(n_buck)],
           "others": [env["add"](vec(False), label="not_buck")[1] for _ in range(n_not)]}
    return out


def gallery(**kw):
    args = dict(brand=[], camera_id=[], species=[], date_from=None, date_to=None, hour_from=None, hour_to=None,
                include_empty=False, favorites=False, bucks=False, before_ts=None, before_id=None, limit=48,
                region_id=1, _=None)
    return cam_router.camera_gallery(**{**args, **kw})["items"]


def test_model_stays_off_until_enough_of_each(env):
    labeled_set(env, bucks.MIN_EACH - 1, bucks.MIN_EACH + 5)
    _sid, d = env["add"](vec(True))
    m = bucks.train()
    assert m["enabled"] is False and m["cv_accuracy"] is None
    assert det(d).buck_score is None
    assert gallery(bucks=True) and all(i["buck_count"] for i in gallery(bucks=True))   # only tagged bucks
    assert len(gallery(bucks=True)) == bucks.MIN_EACH - 1


def test_trained_model_marks_new_bucks(env):
    labeled_set(env, 50, 60)
    _s1, buck = env["add"](vec(True), when=NOW + timedelta(minutes=2))     # newest, so on the first gallery page
    _s2, other = env["add"](vec(False), when=NOW + timedelta(minutes=1))
    m = bucks.train()
    assert m["enabled"] is True and m["cv_accuracy"] >= bucks.MIN_CV_ACCURACY
    assert det(buck).buck_score >= bucks.SHOW_SCORE and det(other).buck_score < 0.5
    ids = {i["id"] for i in gallery(bucks=True)}
    assert _s1 in ids and _s2 not in ids


def test_a_model_that_fails_the_accuracy_check_stays_off(env):
    for i in range(100):                                   # labels unrelated to the vectors
        env["add"](vec(i % 3 == 0), label="buck" if i % 2 else "not_buck")
    m = bucks.train()
    assert m["enabled"] is False and m["cv_accuracy"] is not None
    with Session(engine) as s:
        assert all(v is None for v in s.scalars(select(DeerDetection.buck_score)).all())


def test_the_minimum_accuracy_is_a_user_setting_applied_without_retraining(env, monkeypatch):
    settings = {"buck_min_accuracy_pct": 85}
    monkeypatch.setattr(bucks, "get_settings", lambda: dict(settings, camera_image_dir=""))
    labeled_set(env, 50, 60)
    _s, buck = env["add"](vec(True))
    m = bucks.train()
    assert m["enabled"] is True and bucks.status(1)["min_accuracy"] == 0.85

    settings["buck_min_accuracy_pct"] = 99.9                     # out of range: clamped to 99%
    assert bucks.min_accuracy() == 0.99
    bucks._save_model({**m, "cv_accuracy": 0.9})                  # a model measured at 90%
    bucks._model_cache["mtime"] = None
    settings["buck_min_accuracy_pct"] = 95                        # stricter than the model: it turns off
    assert bucks.apply_min_accuracy()["enabled"] is False and det(buck).buck_score is None
    assert "needs 95%" in bucks._summary(bucks.load_model())
    settings["buck_min_accuracy_pct"] = 90                        # met exactly: back on, deer re-scored
    assert bucks.apply_min_accuracy()["enabled"] is True and det(buck).buck_score >= bucks.SHOW_SCORE
    assert "needs" not in bucks._summary(bucks.load_model())


def test_trainer_batch_tapped_are_bucks_the_rest_are_not(env):
    a, b, c = (env["add"]()[1] for _ in range(3))
    bucks.label_batch(1, shown=[a, b, c], bucks=[a])
    assert (det(a).label, det(b).label, det(c).label) == ("buck", "not_buck", "not_buck")
    assert det(a).label_source == "trainer"


def test_viewer_switching_off_a_model_buck_overrides_it(env):
    labeled_set(env, 50, 60)
    _s, buck = env["add"](vec(True))
    _s2, plain = env["add"](vec(False))
    bucks.train()
    assert bucks.set_buck(1, buck, False)["is_buck"] is False
    assert det(buck).label == "not_buck"
    bucks.set_buck(1, plain, False)                          # wasn't a buck: nothing to override
    assert det(plain).label is None
    assert bucks.set_buck(1, plain, True)["is_buck"] is True and det(plain).label_source == "viewer"


def test_labels_are_scoped_to_the_region(env):
    _s, d = env["add"]()
    assert bucks.set_buck(2, d, True) is None
    bucks.label_batch(2, shown=[d], bucks=[d])
    assert det(d).label is None


def test_queue_skips_marked_deer_and_missing_photos(env):
    _s, fresh = env["add"]()
    env["add"](label="buck")
    env["add"](with_file=False)
    assert [i["id"] for i in bucks.queue(1)] == [fresh]


def test_queue_puts_uncertain_deer_first_once_trained(env):
    labeled_set(env, 50, 60)
    _s, sure = env["add"](vec(True))
    _s2, unsure = env["add"](np.zeros(DIM))
    bucks.train()
    assert [i["id"] for i in bucks.queue(1)][:1] == [unsure]


def test_status_counts(env):
    labeled_set(env, 3, 4)
    env["add"]()
    st = bucks.status(1)
    assert (st["n_buck"], st["n_not_buck"], st["waiting"], st["enabled"]) == (3, 4, 1, False)


def test_unscanned_counts_photos_without_usable_deer(env):
    env["add"]()                                               # scanned, has a vector: done
    with Session(engine) as s:
        old = CameraSighting(camera_id=env["cid"], timestamp=iso(NOW), created_at=iso(NOW), is_animal=1,
                             species="White-tailed Deer", image_path="old.jpg")
        broken = CameraSighting(camera_id=env["cid"], timestamp=iso(NOW), created_at=iso(NOW), is_animal=1,
                                species="White-tailed Deer", image_path="broken.jpg")
        coon = CameraSighting(camera_id=env["cid"], timestamp=iso(NOW), created_at=iso(NOW), is_animal=1,
                              species="Raccoon", image_path="coon.jpg")       # a deer may be behind it
        s.add_all([old, broken, coon])
        s.flush()
        s.add(DeerDetection(sighting_id=broken.id, box_json="[0,0,1,1]", det_conf=0.9,
                            species="White-tailed Deer", vec=None))   # features couldn't be read
        s.commit()
    assert bucks.status(1)["unscanned"] == 3
    assert bucks.status(2)["unscanned"] == 0


@pytest.mark.parametrize("old_source", ["convnext_tiny",   # from before DFNE features worked
                                        "dfne"])           # DFNE on the tight box, before 3.39's padded crop
def test_find_deer_refreshes_old_vectors_from_saved_boxes_and_keeps_tags(env, monkeypatch, old_source):
    import asyncio
    from app.cameras import service
    _s, d = env["add"](vec(True), label="buck")
    with Session(engine) as s:
        s.get(DeerDetection, d).vec_source = old_source
        s.commit()
    assert bucks.status(1)["unscanned"] == 1
    calls = []
    monkeypatch.setattr(detection, "deer_vectors", lambda path, boxes: calls.append(boxes) or
                        [(list(vec(True)), detection.FEATURE_SOURCE)] * len(boxes))
    monkeypatch.setattr(detection, "detect_animal", lambda path: pytest.fail("detector should not run"))
    trained = []
    monkeypatch.setattr(bucks, "train", lambda *a: trained.append(1))   # (in-memory SQLite is per thread)

    class P:
        def set_total(self, n): pass
        def begin(self, name): pass
        def step(self): pass
        def fail(self, key, msg): pass

    out = asyncio.run(service.find_deer_job(P(), 1))
    assert calls == [[[0.1, 0.1, 0.6, 0.9]]] and "updated 1" in out and trained == [1]
    assert (det(d).vec_source, det(d).label) == (detection.FEATURE_SOURCE, "buck")
    assert bucks.status(1)["unscanned"] == 0
