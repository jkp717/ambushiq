import asyncio
import json

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

import app.main  # noqa: F401  (imports every router/model so create_all sees all tables)
from app.core.database import Base, engine
from app.jobs import router as jobs_router
from app.jobs import service
from app.jobs.models import JobRun
from app.regions.models import Region
from app.stands import service as stands_service
from app.stands.models import Stand


@pytest.fixture()
def db():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    service._running.clear()
    with Session(engine) as s:
        for rid in (1, 2):
            s.add(Region(id=rid, name=f"R{rid}", lat=34.7, lon=-92.3, rut_peak_month=12, rut_peak_day=5,
                         property_timezone="America/Chicago", is_default=int(rid == 1), created_at=""))
        s.add(Stand(region_id=1, name="Alpha", lat=34.70, lon=-92.30))
        s.add(Stand(region_id=1, name="Bravo", lat=34.71, lon=-92.31, is_active=0))
        s.add(Stand(region_id=1, name="Charlie", lat=34.72, lon=-92.32))
        s.add(Stand(region_id=2, name="Elsewhere", lat=35.0, lon=-93.0))
        s.commit()


def _fake_jobs(monkeypatch, **fns):
    jobs = {jid: {"label": jid, "description": "", "icon": "x", "fn": fn, "region_scoped": scoped}
            for jid, (fn, scoped) in fns.items()}
    monkeypatch.setattr(service, "registry", lambda: jobs)


def _last(job_id):
    with Session(engine) as s:
        return service._run_dict(s.scalars(select(JobRun).where(JobRun.job_id == job_id)
                                           .order_by(JobRun.id.desc())).first())


async def _items(progress, outcomes):
    progress.set_total(len(outcomes))
    for i, ok in enumerate(outcomes):
        progress.begin(f"item{i}")
        progress.step() if ok else progress.fail(f"item{i}", "boom")
    return "summary"


def test_success_partial_and_failed_statuses(db, monkeypatch):
    async def all_ok(p): return await _items(p, [True, True, True])
    async def one_bad(p): return await _items(p, [True, False, True])
    async def all_bad(p): return await _items(p, [False, False])
    async def raises(p): raise RuntimeError("service down")
    _fake_jobs(monkeypatch, ok=(all_ok, False), part=(one_bad, False), bad=(all_bad, False), err=(raises, False))

    for jid in ("ok", "part", "bad", "err"):
        asyncio.run(service.run_tracked(jid))
    ok, part, bad, err = (_last(j) for j in ("ok", "part", "bad", "err"))
    assert (ok["status"], ok["done"], ok["total"], ok["message"]) == ("success", 3, 3, "summary")
    assert ok["finished_at"] and ok["current"] is None
    assert (part["status"], part["done"], part["failed"]) == ("partial", 2, 1)
    assert part["errors"] == [{"item": "item1", "error": "boom"}]
    assert bad["status"] == "failed"
    assert (err["status"], err["message"]) == ("failed", "service down")
    assert not service._running


def test_sync_job_runs_in_a_thread(db, monkeypatch):
    _fake_jobs(monkeypatch, sync=(lambda p: "done", False))
    asyncio.run(service.run_tracked("sync"))
    assert (_last("sync")["status"], _last("sync")["message"]) == ("success", "done")


def test_second_run_is_rejected_while_running(db, monkeypatch):
    async def slow(p):
        await asyncio.sleep(0.05)
    _fake_jobs(monkeypatch, slow=(slow, False))

    async def scenario():
        first = await jobs_router.run_job("slow", region_id=1)
        assert first["status"] == "running"
        with pytest.raises(HTTPException) as e:
            await jobs_router.run_job("slow", region_id=1)
        assert e.value.status_code == 409
        await service.run_tracked("slow")          # a scheduled firing is skipped, not queued
        await asyncio.gather(*service._tasks)
    asyncio.run(scenario())
    with Session(engine) as s:
        assert len(s.scalars(select(JobRun)).all()) == 1
    assert _last("slow")["status"] == "success"


def test_unknown_job_is_404(db):
    with pytest.raises(HTTPException) as e:
        asyncio.run(jobs_router.run_job("nope", region_id=1))
    assert e.value.status_code == 404


def test_restart_marks_running_rows_interrupted(db):
    with Session(engine) as s:
        s.add(JobRun(job_id="sync_cameras", trigger="scheduled", status="running", started_at="x", done=0, failed=0))
        s.commit()
    service.mark_interrupted()
    run = _last("sync_cameras")
    assert run["status"] == "failed" and "interrupted" in run["message"]


def test_old_runs_are_pruned(db, monkeypatch):
    async def noop(p): pass
    _fake_jobs(monkeypatch, n=(noop, False))
    for _ in range(service.KEEP_RUNS + 5):
        asyncio.run(service.run_tracked("n"))
    with Session(engine) as s:
        assert len(s.scalars(select(JobRun).where(JobRun.job_id == "n")).all()) == service.KEEP_RUNS


def test_terrain_job_covers_the_region_and_keeps_going_past_a_failure(db, monkeypatch):
    async def fake_fetch(lat, lon, progress_callback=None):
        if lat == 34.71:
            raise ValueError("usgs failed")
        return {"downhill_deg": 90, "flat": False, "dem": [[1]], "lat": lat}
    monkeypatch.setattr(stands_service.terrain_mod, "fetch_terrain", fake_fetch)

    asyncio.run(service.run_tracked("terrain_reanalyze", "manual", region_id=1))
    run = _last("terrain_reanalyze")
    assert (run["status"], run["region_id"], run["total"], run["done"], run["failed"]) == ("partial", 1, 3, 2, 1)
    assert run["errors"] == [{"item": "Bravo", "error": "usgs failed"}]
    with Session(engine) as s:
        by_name = {st.name: st for st in s.scalars(select(Stand)).all()}
    assert json.loads(by_name["Alpha"].terrain_json)["lat"] == 34.70 and by_name["Alpha"].downhill_deg == 90
    assert by_name["Bravo"].terrain_json is None
    assert by_name["Elsewhere"].terrain_json is None   # other region untouched


def test_job_list_shows_the_active_regions_terrain_run(db, monkeypatch):
    async def fake_fetch(lat, lon, progress_callback=None):
        return {"downhill_deg": 0, "flat": True}
    monkeypatch.setattr(stands_service.terrain_mod, "fetch_terrain", fake_fetch)
    asyncio.run(service.run_tracked("terrain_reanalyze", "manual", region_id=1))

    jobs = {j["id"]: j for j in jobs_router.list_jobs(region_id=1)}
    assert list(jobs)[0] == "terrain_reanalyze" and "sync_cameras" in jobs and "prune_roads" in jobs
    assert jobs["terrain_reanalyze"]["last_run"]["status"] == "success"
    assert jobs["terrain_reanalyze"]["running"] is False
    assert jobs["sync_cameras"]["last_run"] is None
    other = {j["id"]: j for j in jobs_router.list_jobs(region_id=2)}
    assert other["terrain_reanalyze"]["last_run"] is None
