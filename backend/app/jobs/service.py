"""Job registry + runner. Every job — manual "Run now" or a scheduled firing — goes through
run_tracked()/start_background(), which records a JobRun row (running → success / partial /
failed) so Settings → Jobs can show what is running and how the last run went.

Job functions take a JobProgress as their first argument (plus region_id for region-scoped
jobs) and may return a short summary string, stored as the run's message."""
from __future__ import annotations

import asyncio
import inspect
import json
import logging
from datetime import datetime, timezone

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.database import engine
from app.jobs.models import JobRun

log = logging.getLogger(__name__)

MAX_ERRORS = 50    # per run; the count keeps going, the stored list doesn't
KEEP_RUNS = 20     # per job (and region), older rows are pruned

_running: set[tuple[str, int | None]] = set()
_tasks: set[asyncio.Task] = set()   # strong refs so background runs aren't garbage-collected


class JobBusy(Exception):
    """The job (for this region) is already running."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def registry() -> dict[str, dict]:
    """Jobs in display order. Built on demand: the job functions live in modules (scheduler,
    stands) that would otherwise import-cycle with this one."""
    from app import scheduler as sch
    from app.stands.service import terrain_reanalyze_job
    return {
        "terrain_reanalyze": {
            "label": "Re-analyze stand terrain", "icon": "terrain", "region_scoped": True, "fn": terrain_reanalyze_job,
            "description": "Refreshes slope, drainage and lee-eddy terrain for every stand in this region.",
        },
        "sync_cameras": {
            "label": "Sync trail cameras", "icon": "camera", "fn": sch.sync_cameras_job,
            "description": "Downloads new photos from every active camera and runs deer detection.",
        },
        "warm_forecast": {
            "label": "Refresh weather forecast", "icon": "weather", "fn": sch.warm_forecast_job,
            "description": "Re-fetches the forecast and historical baselines for every region.",
        },
        "auto_cleanup": {
            "label": "Delete old camera photos", "icon": "cleanup", "fn": sch.auto_cleanup_job,
            "description": "Removes photo files older than the retention setting; sighting records are kept.",
        },
        "prune_public_lands": {
            "label": "Prune public-land cache", "icon": "cleanup", "fn": sch.prune_public_lands_job,
            "description": "Drops cached public-land map data nobody has viewed in ~6 months.",
        },
        "prune_trails": {
            "label": "Prune trails cache", "icon": "cleanup", "fn": sch.prune_trails_job,
            "description": "Drops cached trail map data nobody has viewed in ~6 months.",
        },
        "prune_recsites": {
            "label": "Prune recreation-site cache", "icon": "cleanup", "fn": sch.prune_recsites_job,
            "description": "Drops cached recreation-site map data nobody has viewed in ~6 months.",
        },
        "prune_roads": {
            "label": "Prune roads cache", "icon": "cleanup", "fn": sch.prune_roads_job,
            "description": "Drops cached road map data nobody has viewed in ~6 months.",
        },
    }


class JobProgress:
    """Handed to a job function to report progress; every update is written to its JobRun row."""

    def __init__(self, run_id: int):
        self.run_id = run_id
        self.total: int | None = None
        self.done = 0       # items that succeeded
        self.failed = 0     # items that failed
        self.current: str | None = None
        self.errors: list[dict] = []

    def set_total(self, n: int) -> None:
        self.total = n
        self._save()

    def begin(self, item: str) -> None:
        self.current = item
        self._save()

    def step(self) -> None:
        self.done += 1
        self._save()

    def fail(self, item: str, error: str) -> None:
        self.failed += 1
        if len(self.errors) < MAX_ERRORS:
            self.errors.append({"item": item, "error": error[:300]})
        self._save()

    def _save(self, **extra) -> None:
        with Session(engine) as s:
            run = s.get(JobRun, self.run_id)
            if not run:
                return
            run.total, run.done, run.failed, run.current = self.total, self.done, self.failed, self.current
            run.errors_json = json.dumps(self.errors) if self.errors else None
            for k, v in extra.items():
                setattr(run, k, v)
            s.commit()


def _run_dict(r: JobRun | None) -> dict | None:
    if r is None:
        return None
    return {
        "id": r.id, "job_id": r.job_id, "region_id": r.region_id, "trigger": r.trigger, "status": r.status,
        "started_at": r.started_at, "finished_at": r.finished_at, "total": r.total, "done": r.done,
        "failed": r.failed, "current": r.current, "message": r.message,
        "errors": json.loads(r.errors_json) if r.errors_json else [],
    }


def _key(job: dict, job_id: str, region_id: int | None) -> tuple[str, int | None]:
    return job_id, (region_id if job.get("region_scoped") else None)


def _start(job_id: str, trigger: str, region_id: int | None) -> tuple[dict, dict] | None:
    """Claim the job and insert its running row; None if it is already running."""
    job = registry()[job_id]   # KeyError → unknown job
    key = _key(job, job_id, region_id)
    if key in _running:
        return None
    _running.add(key)
    with Session(engine) as s:
        run = JobRun(job_id=job_id, region_id=key[1], trigger=trigger, status="running",
                     started_at=_now(), done=0, failed=0)
        s.add(run)
        s.commit()
        s.refresh(run)
        return job, _run_dict(run)


async def _execute(job: dict, run: dict) -> None:
    progress = JobProgress(run["id"])
    kwargs = {"region_id": run["region_id"]} if job.get("region_scoped") else {}
    status, message = "failed", None
    try:
        if inspect.iscoroutinefunction(job["fn"]):
            result = await job["fn"](progress, **kwargs)
        else:
            result = await asyncio.to_thread(job["fn"], progress, **kwargs)
        message = result if isinstance(result, str) else None
        if progress.failed and not progress.done:
            status = "failed"
        elif progress.failed:
            status = "partial"
        else:
            status = "success"
    except Exception as e:
        log.exception("job %s failed", run["job_id"])
        message = str(e) or type(e).__name__
    finally:
        _running.discard((run["job_id"], run["region_id"]))
        try:
            progress.current = None
            progress._save(status=status, message=message, finished_at=_now())
            _prune(run["job_id"], run["region_id"])
        except Exception:
            log.exception("job %s: couldn't record its result", run["job_id"])


def _runs_of(job_id: str, region_id: int | None):
    """WHERE clause for one job's runs (in one region, for region-scoped jobs)."""
    region = JobRun.region_id.is_(None) if region_id is None else JobRun.region_id == region_id
    return (JobRun.job_id == job_id) & region


def _prune(job_id: str, region_id: int | None) -> None:
    with Session(engine) as s:
        where = _runs_of(job_id, region_id)
        keep = s.scalars(select(JobRun.id).where(where).order_by(JobRun.id.desc()).limit(KEEP_RUNS)).all()
        s.execute(delete(JobRun).where(where, JobRun.id.not_in(keep)))
        s.commit()


async def run_tracked(job_id: str, trigger: str = "scheduled", region_id: int | None = None) -> None:
    """Run a job to completion and record it. Used by the scheduler; a firing that lands while
    the same job is already running (e.g. a manual run) is skipped."""
    claimed = _start(job_id, trigger, region_id)
    if claimed is None:
        log.info("job %s already running — skipped %s run", job_id, trigger)
        return
    await _execute(*claimed)


def start_background(job_id: str, region_id: int | None = None) -> dict:
    """Start a manual run without waiting for it; returns the new run. Raises JobBusy / KeyError."""
    claimed = _start(job_id, "manual", region_id)
    if claimed is None:
        raise JobBusy(job_id)
    task = asyncio.create_task(_execute(*claimed))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    return claimed[1]


def mark_interrupted() -> None:
    """At startup: a run still marked running was cut off by a restart."""
    with Session(engine) as s:
        for run in s.scalars(select(JobRun).where(JobRun.status == "running")).all():
            run.status, run.finished_at = "failed", _now()
            run.message, run.current = "interrupted by server restart", None
        s.commit()


def _schedule(job_id: str) -> tuple[str, str | None]:
    """(human schedule, next run ISO) from the live scheduler, or ("Manual", None)."""
    from app import scheduler as sch
    j = sch._scheduler.get_job(job_id) if sch._scheduler is not None else None
    if j is None:
        return "Manual", None
    interval = getattr(j.trigger, "interval", None)
    if interval is not None:
        mins = round(interval.total_seconds() / 60)
        label = f"Every {mins // 60} h" if mins >= 60 and mins % 60 == 0 else f"Every {mins} min"
    else:
        label = "Daily"
    return label, j.next_run_time.isoformat() if j.next_run_time else None


def job_list(region_id: int | None) -> list[dict]:
    out = []
    with Session(engine) as s:
        for job_id, job in registry().items():
            rid = region_id if job.get("region_scoped") else None
            last = s.scalars(select(JobRun).where(_runs_of(job_id, rid)).order_by(JobRun.id.desc()).limit(1)).first()
            schedule, next_run = _schedule(job_id)
            out.append({
                "id": job_id, "label": job["label"], "description": job["description"], "icon": job["icon"],
                "region_scoped": bool(job.get("region_scoped")), "schedule": schedule, "next_run_at": next_run,
                "running": (job_id, rid) in _running, "last_run": _run_dict(last),
            })
    return out
