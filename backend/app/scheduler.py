"""Background jobs (APScheduler): periodic camera sync + nightly image cleanup."""
from __future__ import annotations

import datetime as _dt
import logging
import os
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.cameras import detection as detection_mod
from app.cameras.models import Camera, CameraSighting
from app.cameras.service import _sync_one_camera
from app.core.database import engine
from app.settings.service import get_settings

log = logging.getLogger(__name__)


async def sync_cameras_job():
    """Scheduler job: sync every active camera, then release the detection
    models — this box has limited RAM, so MegaDetector + the species
    classifier are only kept resident for the duration of one sync round
    (and only load at all if a camera actually has a new photo to process)."""
    with Session(engine) as s:
        ids = [c.id for c in s.scalars(select(Camera).where(Camera.is_active == 1)).all()]
    try:
        for cid in ids:
            try:
                res = await _sync_one_camera(cid)
                if res.get("new") or res.get("no_animal") or res.get("failed"):
                    log.info("scheduler: cam %s — %d new sighting(s), %d with no animal, %d failed",
                             cid, res.get("new", 0), res.get("no_animal", 0), res.get("failed", 0))
            except Exception:
                continue
    finally:
        detection_mod.unload_models()


async def warm_forecast_job():
    """Scheduler job: refresh the cached forecast + historical baselines for every region."""
    from app.forecast.service import warm_forecasts
    await warm_forecasts()


def prune_public_lands_job():
    """Scheduler job (daily): drop cached public-land grid cells nobody has needed for ~6 months."""
    from app.publiclands.service import prune_cache
    n = prune_cache()
    if n:
        log.info("scheduler: pruned %d stale public land cache cell(s)", n)


def prune_trails_job():
    """Scheduler job (daily): drop cached trail/road grid cells nobody has needed for ~6 months."""
    from app.trails.service import prune_cache
    n = prune_cache()
    if n:
        log.info("scheduler: pruned %d stale trail cache cell(s)", n)


def prune_recsites_job():
    """Scheduler job (daily): drop cached recreation-site grid cells nobody has needed for ~6 months."""
    from app.recsites.service import prune_cache
    n = prune_cache()
    if n:
        log.info("scheduler: pruned %d stale recreation site cache cell(s)", n)


def prune_roads_job():
    """Scheduler job (daily): drop cached road grid cells nobody has needed for ~6 months."""
    from app.roads.service import prune_cache
    n = prune_cache()
    if n:
        log.info("scheduler: pruned %d stale road cache cell(s)", n)


def auto_cleanup_job():
    """Scheduler job (daily 3 AM UTC): delete JPEGs older than retention; keep
    sighting rows. Runs at a fixed UTC hour rather than any one region's local
    time — with multiple regions there's no longer a single canonical "local
    3 AM," and this is a background maintenance job, not user-facing, so exact
    fire time doesn't matter."""
    import datetime as _dt
    settings = get_settings()
    retention = int(settings.get("image_retention_days", 60))
    cutoff = datetime.now(timezone.utc) - _dt.timedelta(days=retention)
    with Session(engine) as s:
        rows = s.scalars(select(CameraSighting).where(CameraSighting.image_path.isnot(None))).all()
        for row in rows:
            raw_ts = row.created_at or row.timestamp
            if not raw_ts:
                continue
            try:
                created = datetime.fromisoformat(raw_ts.replace("Z", "+00:00"))
                if created.tzinfo is None:
                    created = created.replace(tzinfo=timezone.utc)
            except Exception:
                continue
            if created < cutoff:
                if row.image_path and os.path.exists(row.image_path):
                    try:
                        os.remove(row.image_path)
                    except OSError:
                        pass
                row.image_path = None  # keep the sighting for model auto-tuning
        s.commit()


_scheduler = None


def _reschedule_sync(interval_minutes: int) -> None:
    """Live-update the camera sync job interval without restarting the process.
    Called from write_settings so changes take effect immediately."""
    if _scheduler is None:
        return
    try:
        from apscheduler.triggers.interval import IntervalTrigger
        _scheduler.reschedule_job(
            "sync_cameras",
            trigger=IntervalTrigger(minutes=max(1, interval_minutes)),
        )
    except Exception:
        pass  # best-effort; scheduler may not be running yet


def start_scheduler():
    """Start APScheduler with the sync + cleanup jobs. Lazy import so app boots even
    if apscheduler isn't installed (jobs simply won't run)."""
    global _scheduler
    if _scheduler is not None:
        return
    try:
        from apscheduler.schedulers.asyncio import AsyncIOScheduler
        from apscheduler.triggers.interval import IntervalTrigger
        from apscheduler.triggers.cron import CronTrigger
    except Exception:
        return
    settings = get_settings()
    interval = int(settings.get("camera_sync_interval_minutes", 30)) or 30
    sched = AsyncIOScheduler()
    sched.add_job(sync_cameras_job, IntervalTrigger(minutes=interval), id="sync_cameras",
                  replace_existing=True, max_instances=1)
    sched.add_job(auto_cleanup_job, CronTrigger(hour=3, minute=0), id="auto_cleanup",
                  replace_existing=True, max_instances=1)
    sched.add_job(prune_public_lands_job, CronTrigger(hour=3, minute=30), id="prune_public_lands",
                  replace_existing=True, max_instances=1)
    sched.add_job(prune_trails_job, CronTrigger(hour=3, minute=45), id="prune_trails",
                  replace_existing=True, max_instances=1)
    sched.add_job(prune_recsites_job, CronTrigger(hour=4, minute=0), id="prune_recsites",
                  replace_existing=True, max_instances=1)
    sched.add_job(prune_roads_job, CronTrigger(hour=4, minute=15), id="prune_roads",
                  replace_existing=True, max_instances=1)
    # Keep the weather cache warm: first run shortly after boot (so a restart never leaves the
    # first user waiting on the provider), then every 30 minutes.
    sched.add_job(warm_forecast_job, IntervalTrigger(minutes=30), id="warm_forecast",
                  replace_existing=True, max_instances=1, coalesce=True,
                  next_run_time=datetime.now(timezone.utc) + _dt.timedelta(seconds=5))
    sched.start()
    _scheduler = sched
