import asyncio
import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

import app.main  # noqa: F401  (registers every model so create_all sees all tables)
from app.cameras import providers, router as cam_router, service
from app.cameras.models import Camera, CameraSighting
from app.core.database import Base, engine
from app.regions.models import Region

NOW = datetime.now(timezone.utc).replace(microsecond=0)


def iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def photo(pid, taken, body="animal", cam="cam-A", url=None):
    return {"id": pid, "url": url or f"https://img.example/{body}/{pid}.jpg?sig=abc", "taken_at": iso(taken), "camera_ref": cam}


class FakeProvider:
    implemented = True

    def __init__(self, photos):
        self.photos, self.calls = photos, []

    async def fetch_recent_photos(self, since=None, camera_ref=None):
        self.calls.append({"since": since, "camera_ref": camera_ref})
        return list(self.photos)

    async def fetch_cameras(self):
        return []


@pytest.fixture()
def env(monkeypatch, tmp_path):
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        s.add(Region(id=1, name="r", lat=34.7, lon=-92.3, property_timezone="America/Chicago"))
        cam = Camera(region_id=1, name="North Field", brand="spypoint", provider_ref="cam-A", is_active=1,
                     is_deleted=0, created_at=iso(NOW))
        s.add(cam)
        s.commit()
        cid = cam.id

    state = {"downloads": [], "fail": set()}

    def download(request):
        state["downloads"].append(request.url.path)
        if any(f in request.url.path for f in state["fail"]):
            return httpx.Response(500)
        return httpx.Response(200, content=request.url.path.split("/")[1].encode())   # body = the /animal/ or /empty/ segment

    real_client = httpx.AsyncClient
    monkeypatch.setattr(service.httpx, "AsyncClient", lambda **kw: real_client(transport=httpx.MockTransport(download), **kw))
    monkeypatch.setattr(service, "get_camera_dir", lambda *a, **k: str(tmp_path))
    monkeypatch.setattr(service, "get_settings", lambda: {"camera_backfill_days": 7})

    def detect(path):
        body = open(path, "rb").read()
        if body == b"empty":
            return {"is_animal": False, "confidence": 0.05, "detector": "megadetector", "species": None, "species_confidence": None}
        if body == b"boom":
            return {"is_animal": False, "confidence": 0.0, "detector": "error (ValueError)", "species": None, "species_confidence": None}
        return {"is_animal": True, "confidence": 0.9, "detector": "megadetector", "species": "White-tailed Deer", "species_confidence": 0.8}
    monkeypatch.setattr(service.detection_mod, "detect_animal", detect)

    def use(photos):
        prov = photos if isinstance(photos, FakeProvider) else FakeProvider(photos)
        monkeypatch.setattr(service.cameras_mod, "get_provider", lambda *_a, **_k: prov)
        return prov

    state["use"], state["cid"] = use, cid
    return state


def sync(env, **kw):
    return asyncio.run(service._sync_one_camera(env["cid"], **kw))


def rows():
    with Session(engine) as s:
        return s.scalars(select(CameraSighting).order_by(CameraSighting.id)).all()


def camera():
    with Session(engine) as s:
        return s.scalars(select(Camera)).first()


def test_photos_stamped_in_the_same_second_are_all_kept(env):
    t = NOW - timedelta(hours=2)
    env["use"]([photo("a", t), photo("b", t), photo("c", t)])
    out = sync(env)
    assert out["new"] == 3 and len(rows()) == 3 and len({r.provider_photo_id for r in rows()}) == 3


def test_photos_with_no_animal_are_kept_but_flagged(env):
    env["use"]([photo("a", NOW - timedelta(hours=2), body="empty"), photo("b", NOW - timedelta(hours=1))])
    out = sync(env)
    assert (out["new"], out["no_animal"]) == (1, 1)
    empty = next(r for r in rows() if not r.is_animal)
    assert empty.species is None and empty.image_path
    import os
    assert os.path.exists(empty.image_path)                                    # the file is kept, not deleted


def test_no_animal_photos_stay_out_of_the_activity_graph_and_default_gallery(env):
    env["use"]([photo("a", NOW - timedelta(hours=2), body="empty"), photo("b", NOW - timedelta(hours=1))])
    sync(env)
    args = dict(brand=[], camera_id=[], species=[], date_from=None, date_to=None, region_id=1, _=None)
    assert cam_router.camera_activity(**args)["total"] == 1
    gal = dict(args, hour_from=None, hour_to=None, before_ts=None, before_id=None, limit=48)
    assert len(cam_router.camera_gallery(include_empty=False, **gal)["items"]) == 1
    both = cam_router.camera_gallery(include_empty=True, **gal)["items"]
    assert len(both) == 2 and {i["is_animal"] for i in both} == {True, False}


def test_detector_errors_are_still_recorded_as_sightings(env):
    env["use"]([photo("a", NOW - timedelta(hours=1), body="boom")])
    out = sync(env)
    assert out["detection_errors"] == 1 and len(rows()) == 1 and rows()[0].is_animal == 1


def test_a_photo_taken_before_the_last_sync_but_uploaded_after_it_is_still_imported(env):
    env["use"]([photo("early", NOW - timedelta(hours=6))])
    sync(env)
    late = photo("late", NOW - timedelta(hours=5))                             # taken before the sync above finished
    prov = env["use"]([photo("early", NOW - timedelta(hours=6)), late])
    out = sync(env)
    assert out["new"] == 1 and {r.provider_photo_id for r in rows()} == {"id:early", "id:late"}
    cursor = camera().sync_cursor_at
    assert prov.calls[0]["since"] <= datetime.fromisoformat(cursor) - timedelta(days=2, hours=23)   # re-lists the overlap


def test_relisted_photos_are_not_downloaded_again(env):
    env["use"]([photo("a", NOW - timedelta(hours=3)), photo("b", NOW - timedelta(hours=2), body="empty")])
    sync(env)
    first = len(env["downloads"])
    out = sync(env)
    assert first == 2 and len(env["downloads"]) == 2 and out["fetched"] == 0 and len(rows()) == 2


def test_photos_without_an_id_are_identified_by_their_url_not_the_signed_query(env):
    p1 = photo(None, NOW - timedelta(hours=3), url="https://img.example/animal/x.jpg?sig=ONE")
    p1["id"] = None
    env["use"]([p1])
    sync(env)
    p2 = dict(p1, url="https://img.example/animal/x.jpg?sig=TWO")
    env["use"]([p2])
    sync(env)
    assert len(rows()) == 1 and rows()[0].provider_photo_id == "url:img.example/animal/x.jpg"


def test_a_failed_download_holds_the_cursor_back_and_is_retried(env):
    taken = NOW - timedelta(hours=1)
    env["use"]([photo("ok", NOW - timedelta(hours=2)), photo("bad", taken)])
    env["fail"].add("bad.jpg")
    out = sync(env)
    assert out["failed"] == 1 and out["new"] == 1
    assert datetime.fromisoformat(camera().sync_cursor_at) == taken - timedelta(seconds=1)
    env["fail"].clear()                                                        # the download works next time
    assert sync(env)["new"] == 1 and len(rows()) == 2
    assert datetime.fromisoformat(camera().sync_cursor_at) > NOW - timedelta(minutes=1)     # cursor released


def test_a_photo_that_never_downloads_is_given_up_on_after_a_week(env):
    env["use"]([photo("ancient", NOW - timedelta(days=9))])
    env["fail"].add("ancient.jpg")
    out = sync(env)
    assert out["failed"] == 1
    assert datetime.fromisoformat(camera().sync_cursor_at) > NOW - timedelta(minutes=1)     # not held back forever


def test_rows_saved_before_photo_ids_existed_are_adopted_not_duplicated(env):
    t = NOW - timedelta(hours=4)
    with Session(engine) as s:                                                  # an old row: timestamp only
        s.add(CameraSighting(camera_id=env["cid"], timestamp=iso(t), confidence_score=0.9, species="Coyote", is_animal=1))
        s.commit()
    env["use"]([photo("a", t), photo("b", t)])                                  # a same-second burst of two
    out = sync(env)
    stored = rows()
    assert out["new"] == 1 and len(stored) == 2                                 # one matched the old row, one is new
    assert sorted(r.provider_photo_id for r in stored) == ["id:a", "id:b"]
    assert sync(env)["fetched"] == 0                                            # and both are known from now on


def test_reimport_reaches_back_the_requested_number_of_days(env):
    prov = env["use"]([])
    sync(env)                                                                   # normal sync sets a recent cursor
    sync(env, since_days=30)
    assert abs((NOW - prov.calls[1]["since"]) - timedelta(days=30)) < timedelta(minutes=5)


def test_only_this_cameras_photos_are_imported_and_the_request_is_scoped_to_it(env):
    prov = env["use"]([photo("mine", NOW - timedelta(hours=1)), photo("theirs", NOW - timedelta(hours=1), cam="cam-B")])
    out = sync(env)
    assert out["new"] == 1 and prov.calls[0]["camera_ref"] == "cam-A"


def test_an_unreachable_provider_leaves_the_cursor_alone(env):
    class Down(FakeProvider):
        async def fetch_recent_photos(self, since=None, camera_ref=None):
            raise httpx.ReadTimeout("slow")
    env["use"](Down([]))
    before = camera().sync_cursor_at
    assert sync(env)["new"] == 0 and camera().sync_cursor_at == before


# ---------- SpyPoint: the 500-photo response cap ----------

def test_spypoint_splits_the_date_window_when_the_photo_limit_is_hit(monkeypatch):
    base = NOW - timedelta(days=6)
    all_photos = [{"id": f"p{i}", "camera": "cam-A", "date": iso(base + timedelta(hours=7 * i)),
                   "large": {"host": "h.example", "path": f"p{i}.jpg"}} for i in range(20)]

    def handler(request):
        if request.url.path.endswith("/camera/all"):
            return httpx.Response(200, json=[{"id": "cam-A", "config": {"name": "A"}}])
        body = json.loads(request.content)
        assert body["camera"] == ["cam-A"]                                      # scoped to the one camera
        begin = datetime.fromisoformat(body["dateBegin"].replace("Z", "+00:00"))
        end = datetime.fromisoformat(body["dateEnd"].replace("Z", "+00:00"))
        hits = [p for p in all_photos if begin <= datetime.fromisoformat(p["date"].replace("Z", "+00:00")) < end]
        return httpx.Response(200, json={"photos": hits[:body["limit"]]})       # the server silently truncates

    real = httpx.AsyncClient
    monkeypatch.setattr(providers.httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    monkeypatch.setattr(providers.SpyPointProvider, "_login", lambda self, client: _async("tok"))
    monkeypatch.setattr(providers.SpyPointProvider, "PHOTO_LIMIT", 5)
    prov = providers.SpyPointProvider({"username": "u", "password": "p"})
    out = asyncio.run(prov.fetch_recent_photos(since=base - timedelta(hours=1), camera_ref="cam-A"))
    assert sorted(p["id"] for p in out) == sorted(p["id"] for p in all_photos)   # nothing lost, nothing doubled


async def _async(value):
    return value
