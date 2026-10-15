"""Alerts for the top-bar bell: camera, system and setup problems worth acting on.

Hunt conditions (swirling winds, rain, high wind) are not alerts: they belong to a particular day and sit,
so they're shown in the day views instead (forecast.hunt). Every alert here is worked out on request from
data the app already keeps; nothing is stored except dismissals.

An alert is {key, category: "camera"|"system", severity: "critical"|"warning"|"info", title, detail,
at (ISO, or None), quiet, stand_id?, camera_id?}. `key` identifies the condition: dismissing hides that
key, and once the condition clears (the key stops appearing) the dismissal is dropped, so the alert shows
again if the condition comes back. Keys that name a specific event (a job run, the latest buck photo)
show again for each new event. `quiet` alerts (a camera not on a stand) don't add to the bell's count."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.alerts.models import AlertDismissal
from app.cameras import bucks as bucks_mod
from app.cameras.models import Camera, CameraSighting, DeerDetection
from app.core.database import engine
from app.deer_sign.models import DeerSign
from app.forecast import scoring
from app.forecast.providers import _sun_times_utc
from app.forecast.service import SUN_MARGIN, _camera_health, _sign_freshness, fetch_status
from app.jobs.models import JobRun
from app.regions import drainage as drainage_mod
from app.regions import terrain as region_terrain
from app.regions.models import Region
from app.settings.service import get_settings
from app.stands.models import Stand

log = logging.getLogger(__name__)

SEVERITY_ORDER = {"critical": 0, "warning": 1, "info": 2}
RECENT_H = 48               # "recent" for buck photos and the deer-spike window
SPIKE_BASELINE_DAYS = 28    # the deer-spike comparison period, before the recent window
SPIKE_MIN = 3               # at least this many daylight deer photos in the recent window...
SPIKE_FACTOR = 2.0          # ...and at least this many times the usual count for 48 h
STALE_SIGN = 0.25           # scrape/rub freshness at or below this barely counts toward scores


def _alert(key, category, severity, title, detail="", at=None, quiet=False, **extra) -> dict:
    return {"key": key, "category": category, "severity": severity, "title": title, "detail": detail,
            "at": at, "quiet": quiet, **extra}


def _parse(ts) -> Optional[datetime]:
    if not ts:
        return None
    try:
        t = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def _tz(region: Region) -> ZoneInfo:
    try:
        return ZoneInfo(region.property_timezone or "America/Chicago")
    except Exception:
        return ZoneInfo("America/Chicago")


def _when(t: datetime, tz: ZoneInfo) -> str:
    """'Oct 15, 7:12 AM' in the property's time zone (portable: no %-d / %-I)."""
    lt = t.astimezone(tz)
    return f"{lt:%b} {lt.day}, {lt.hour % 12 or 12}:{lt:%M} {'AM' if lt.hour < 12 else 'PM'}"


def _ago(hours: float) -> str:
    return f"{int(hours)} h" if hours < 48 else f"{int(hours // 24)} days"


# ---------------------------------------------------------------- camera alerts

def _camera_alerts(s: Session, region: Region, settings: dict, now: datetime) -> list[dict]:
    out = []
    cams = s.scalars(select(Camera).where(Camera.region_id == region.id, Camera.is_active == 1,
                                          Camera.is_deleted == 0)).all()
    max_age = float(settings.get("camera_health_max_age_hours", 48.0) or 48.0)
    bat_thr = float(settings.get("alert_battery_pct", 20))
    sig_thr = float(settings.get("alert_signal_pct", 25))
    sd_thr = float(settings.get("alert_sd_pct", 90)) / 100
    for c in cams:
        ids = {"camera_id": c.id, "stand_id": c.stand_id}
        if not _camera_health(c.last_seen_at, None, None, max_age)["healthy"]:
            seen = _parse(c.last_seen_at)
            hours = (now - seen).total_seconds() / 3600 if seen else 0
            out.append(_alert(f"cam_offline:{c.id}", "camera", "warning", f"{c.name} is offline",
                              f"Hasn't checked in for {_ago(hours)}", c.last_seen_at, **ids))
        if c.photo_limit not in (None, -1, 0) and c.photo_count is not None:
            used = c.photo_count / c.photo_limit
            if used >= 0.9:
                full = used >= 1
                out.append(_alert(f"cam_quota:{c.id}", "camera", "critical" if full else "warning",
                                  f"{c.name} photo plan {'used up' if full else 'nearly full'}",
                                  f"{c.photo_count} of {c.photo_limit} photos this cycle", **ids))
        if (c.battery_pct is not None and c.battery_pct <= bat_thr) or c.battery_level == "low":
            pct = f"{c.battery_pct}%" if c.battery_pct is not None else "Low"
            out.append(_alert(f"cam_battery:{c.id}", "camera",
                              "critical" if c.battery_pct is not None and c.battery_pct <= 10 else "warning",
                              f"{c.name} battery low", pct + (f" ({c.power_type})" if c.power_type else ""), **ids))
        if c.low_signal or (c.signal_pct is not None and c.signal_pct <= sig_thr):
            parts = [f"Signal {c.signal_pct}%" if c.signal_pct is not None else "Weak signal"]
            if c.signal_bars is not None:
                parts.append(f"{c.signal_bars} bar{'' if c.signal_bars == 1 else 's'}")
            out.append(_alert(f"cam_signal:{c.id}", "camera", "warning", f"{c.name} has a weak signal",
                              " · ".join(parts) + ". Photos may arrive late or not at all.", **ids))
        if c.sd_size_mb and c.sd_used_mb is not None and c.sd_used_mb / c.sd_size_mb >= sd_thr:
            frac = c.sd_used_mb / c.sd_size_mb
            out.append(_alert(f"cam_sd:{c.id}", "camera", "critical" if frac >= 0.98 else "warning",
                              f"{c.name} SD card {round(frac * 100)}% full",
                              f"{c.sd_used_mb / 1024:.1f} of {c.sd_size_mb / 1024:.1f} GB used", **ids))
        if c.stand_id is None:
            out.append(_alert(f"cam_unassigned:{c.id}", "camera", "info", f"{c.name} isn't on a stand",
                              "Assign it to a stand in Cameras → Setup so its photos count toward that "
                              "stand's score.", quiet=True, **ids))
    out += _sync_failures(s, cams)
    out += _buck_alerts(s, region, cams, now)
    out += _deer_spikes(s, region, now)
    return out


def _sync_failures(s: Session, cams: list[Camera]) -> list[dict]:
    """Cameras named in the errors of the latest finished camera-sync run."""
    run = s.scalars(select(JobRun).where(JobRun.job_id == "sync_cameras", JobRun.region_id.is_(None),
                                         JobRun.status != "running").order_by(JobRun.id.desc()).limit(1)).first()
    if not run or run.status not in ("failed", "partial") or not run.errors_json:
        return []
    try:
        errors = {e.get("item"): e.get("error") for e in json.loads(run.errors_json) if isinstance(e, dict)}
    except (ValueError, TypeError):
        return []
    return [_alert(f"cam_sync:{c.id}:{run.id}", "camera", "warning", f"{c.name} didn't sync",
                   errors[c.name] or "The last sync failed", run.finished_at, camera_id=c.id, stand_id=c.stand_id)
            for c in cams if c.name in errors]


def _buck_alerts(s: Session, region: Region, cams: list[Camera], now: datetime) -> list[dict]:
    """Bucks (tagged, or marked by the buck model) photographed in the last 48 h, one alert per camera."""
    if not cams:
        return []
    since = now - timedelta(hours=RECENT_H)
    rows = s.execute(select(DeerDetection.id, CameraSighting.camera_id, CameraSighting.timestamp,
                            CameraSighting.id)
                     .join(CameraSighting, CameraSighting.id == DeerDetection.sighting_id)
                     .where(bucks_mod.is_buck_sql(), CameraSighting.camera_id.in_([c.id for c in cams]),
                            CameraSighting.timestamp >= (since - timedelta(days=1)).date().isoformat())).all()
    per_cam: dict[int, dict] = {}
    for det_id, cam_id, ts, sighting_id in rows:
        t = _parse(ts)
        if not t or t < since:
            continue
        g = per_cam.setdefault(cam_id, {"photos": set(), "latest": t, "det": det_id})
        g["photos"].add(sighting_id)
        g["latest"] = max(g["latest"], t)
        g["det"] = max(g["det"], det_id)
    by_id = {c.id: c for c in cams}
    tz = _tz(region)
    out = []
    for cam_id, g in per_cam.items():
        c = by_id[cam_id]
        n = len(g["photos"])
        out.append(_alert(f"buck:{cam_id}:{g['det']}", "camera", "info", f"Buck on {c.name}",
                          f"{n} buck photo{'' if n == 1 else 's'} in the last 48 h, latest {_when(g['latest'], tz)}",
                          g["latest"].isoformat(), camera_id=cam_id, stand_id=c.stand_id))
    return out


def _deer_spikes(s: Session, region: Region, now: datetime) -> list[dict]:
    """Stands whose cameras saw clearly more daylight deer in the last 48 h than usual (the 28 days before)."""
    stands = {st.id: st for st in s.scalars(select(Stand).where(Stand.region_id == region.id,
                                                                  Stand.is_active == 1)).all()}
    if not stands:
        return []
    recent_from = now - timedelta(hours=RECENT_H)
    base_from = recent_from - timedelta(days=SPIKE_BASELINE_DAYS)
    rows = s.execute(select(CameraSighting.stand_id, CameraSighting.timestamp, CameraSighting.species)
                     .where(CameraSighting.stand_id.in_(list(stands)), CameraSighting.is_animal == 1,
                            CameraSighting.timestamp >= (base_from - timedelta(days=1)).date().isoformat())).all()
    tz_name = region.property_timezone or "America/Chicago"
    sun: dict = {}

    def daylight(st: Stand, t: datetime) -> bool:
        # same rule as ScoringContext.daylight_sightings: sunrise - 30 min to sunset + 30 min, local date
        d = t.astimezone(_tz(region)).date()
        if (st.id, d) not in sun:
            sun[(st.id, d)] = _sun_times_utc(st.lat, st.lon, d, tz_name)
        sr, ss = sun[(st.id, d)]
        return not (sr and ss) or sr - SUN_MARGIN <= t <= ss + SUN_MARGIN

    counts: dict[int, list[int]] = {}
    for stand_id, ts, species in rows:
        t = _parse(ts)
        if not t or t < base_from or t > now or not scoring._is_deer_sighting({"species": species}):
            continue
        if not daylight(stands[stand_id], t):
            continue
        c = counts.setdefault(stand_id, [0, 0])
        c[0 if t >= recent_from else 1] += 1
    out = []
    for stand_id, (recent, base) in counts.items():
        usual = base / (SPIKE_BASELINE_DAYS * 24 / RECENT_H)
        if recent >= SPIKE_MIN and recent >= SPIKE_FACTOR * usual:
            usual_txt = f"usually about {usual:.1f}" if usual >= 0.1 else "usually none"
            out.append(_alert(f"deer_spike:{stand_id}:{now.astimezone(_tz(region)).date().isoformat()}",
                              "camera", "info", f"More deer at {stands[stand_id].name}",
                              f"{recent} daylight deer photos in the last 48 h ({usual_txt})",
                              now.isoformat(), stand_id=stand_id))
    return out


# ---------------------------------------------------------------- system and setup alerts

def _job_alerts(s: Session, region: Region, now: datetime) -> list[dict]:
    from app import scheduler as sch
    from app.jobs.service import registry
    jobs = registry()
    out = []
    for job_id, job in jobs.items():
        rid = region.id if job.get("region_scoped") else None
        where = (JobRun.job_id == job_id) & (JobRun.region_id.is_(None) if rid is None else JobRun.region_id == rid)
        run = s.scalars(select(JobRun).where(where, JobRun.status != "running")
                        .order_by(JobRun.id.desc()).limit(1)).first()
        if run and run.status in ("failed", "partial"):
            failed = run.status == "failed"
            out.append(_alert(f"job:{run.id}", "system", "critical" if failed else "warning",
                              f"{job['label']} {'failed' if failed else 'partly failed'}",
                              run.message or (f"{run.failed} item{'' if run.failed == 1 else 's'} failed"
                                              if run.failed else ""), run.finished_at, link="settings"))
    if sch._scheduler is None:
        out.append(_alert("scheduler_down", "system", "critical", "Background jobs aren't running",
                          "Camera sync, forecast refresh and photo cleanup won't run until the app is restarted. "
                          "Check the app's log for why the scheduler didn't start.", link="settings"))
        return out
    started = sch._started_at or now
    for j in sch._scheduler.get_jobs():
        interval = getattr(j.trigger, "interval", None)
        allowed = 2 * interval + timedelta(minutes=15) if interval is not None else timedelta(hours=26)
        last = s.scalars(select(JobRun.started_at).where(JobRun.job_id == j.id, JobRun.region_id.is_(None))
                         .order_by(JobRun.id.desc()).limit(1)).first()
        ref = max(filter(None, [_parse(last), started]))
        if now - ref > allowed:
            label = jobs.get(j.id, {}).get("label", j.id)
            out.append(_alert(f"job_late:{j.id}", "system", "warning", f"{label} hasn't run",
                              f"Last run {_ago((now - ref).total_seconds() / 3600)} ago" if last else
                              "It hasn't run since the app started", last, link="settings"))
    return out


def _forecast_location(s: Session, region: Region) -> Optional[tuple[float, float]]:
    # the same point the forecast is fetched for (forecast.router._forecast_location)
    first = s.scalars(select(Stand).where(Stand.region_id == region.id).order_by(Stand.name)).first()
    if first:
        return first.lat, first.lon
    return (region.lat, region.lon) if (region.lat or region.lon) else None


def _weather_alert(s: Session, region: Region) -> list[dict]:
    loc = _forecast_location(s, region)
    st = fetch_status(*loc) if loc else {}
    if not st.get("error_at") or (st.get("ok_at") or 0) > st["error_at"]:
        return []
    ok = f" Showing the forecast saved {_when(datetime.fromtimestamp(st['ok_at'], timezone.utc), _tz(region))}." \
        if st.get("ok_at") else " Showing the last saved forecast."
    return [_alert("weather_provider", "system", "warning", f"{st.get('provider') or 'Weather service'} isn't responding",
                   f"{st.get('error') or 'The last update failed'}.{ok}",
                   datetime.fromtimestamp(st["error_at"], timezone.utc).isoformat(), link="settings")]


def _setup_alerts(s: Session, region: Region, now: datetime) -> list[dict]:
    out = []
    rows = s.scalars(select(Stand).where(Stand.region_id == region.id, Stand.is_active == 1)).all()
    stands = drainage_mod.with_property_terrain(region.id, [r.to_dict() for r in rows])
    for st in stands:
        if not st.get("terrain") and st.get("downhill_deg") is None:
            out.append(_alert(f"stand_terrain:{st['id']}", "system", "info", f"{st['name']} has no terrain analysis",
                              "Thermals are ignored for this stand. Run Analyze terrain from the stand editor.",
                              stand_id=st["id"]))
        if st.get("deer_approach_deg") is None:
            out.append(_alert(f"stand_approach:{st['id']}", "system", "info",
                              f"{st['name']} has no deer approach",
                              "Scent direction isn't scored. Set the direction deer come from in the stand editor.",
                              stand_id=st["id"]))
    if stands and region_terrain.needs_refresh(region.id):
        none_yet = region_terrain.load_grid(region.id) is None
        out.append(_alert("property_terrain", "system", "info",
                          "Property terrain hasn't been analyzed" if none_yet else "Property terrain is out of date",
                          "Run Analyze property terrain in Settings → Jobs so lee eddies and drainage cover every stand.",
                          link="settings"))
    signs = [r.to_dict() for r in s.scalars(select(DeerSign).where(DeerSign.region_id == region.id,
                                                                    DeerSign.is_active == 1)).all()]
    stale = [g for g in signs if g["kind"] in ("scrape", "rub") and _sign_freshness(g, now) <= STALE_SIGN]
    if stale:
        n_scrape = sum(g["kind"] == "scrape" for g in stale)
        n_rub = len(stale) - n_scrape
        what = " and ".join(p for p in (f"{n_scrape} scrape{'' if n_scrape == 1 else 's'}" if n_scrape else "",
                                         f"{n_rub} rub{'' if n_rub == 1 else 's'}" if n_rub else "") if p)
        out.append(_alert(f"stale_sign:{len(stale)}:{max(g['id'] for g in stale)}", "system", "info",
                          f"{what} getting stale",
                          "Logged long enough ago that they barely count toward stand scores. Re-check them, "
                          "or switch them off in Zones."))
    return out


def _buck_model_alert() -> list[dict]:
    m = bucks_mod.load_model()
    if not m or m.get("enabled") or m.get("cv_accuracy") is None or "w" not in m:
        return []
    return [_alert(f"buck_model:{m.get('trained_at')}", "system", "warning", "Buck model is off",
                   f"Its accuracy ({m['cv_accuracy']:.0%}) is below your {bucks_mod.min_accuracy():.0%} minimum. "
                   "Tag more bucks and retrain in Settings → Model training.", m.get("trained_at"), link="settings")]


# ---------------------------------------------------------------- public

def _evaluate(region_id: int) -> list[dict]:
    now = datetime.now(timezone.utc)
    settings = get_settings()
    out: list[dict] = []
    with Session(engine) as s:
        region = s.get(Region, region_id)
        if region is None:
            return []
        checks = (lambda: _camera_alerts(s, region, settings, now), lambda: _job_alerts(s, region, now),
                  lambda: _weather_alert(s, region), lambda: _setup_alerts(s, region, now), _buck_model_alert)
        for check in checks:
            try:
                out += check()
            except Exception:
                log.exception("alerts: a check failed for region %s; skipping it", region_id)
    return out


def _sort_key(a: dict):
    t = _parse(a.get("at"))
    return (SEVERITY_ORDER.get(a["severity"], 9), a["quiet"], -(t.timestamp() if t else 0))


def current_alerts(region_id: int) -> list[dict]:
    """The region's alerts that aren't dismissed, most severe and newest first. Dismissals whose alert
    has cleared are dropped here, so a condition that comes back shows again."""
    alerts = _evaluate(region_id)
    active = {a["key"] for a in alerts}
    with Session(engine) as s:
        dismissed = set(s.scalars(select(AlertDismissal.key).where(AlertDismissal.region_id == region_id)).all())
        cleared = dismissed - active
        if cleared:
            s.execute(delete(AlertDismissal).where(AlertDismissal.region_id == region_id,
                                                   AlertDismissal.key.in_(cleared)))
            s.commit()
    return sorted((a for a in alerts if a["key"] not in dismissed), key=_sort_key)


def dismiss(region_id: int, key: str) -> None:
    with Session(engine) as s:
        exists = s.scalars(select(func.count()).select_from(AlertDismissal).where(
            AlertDismissal.region_id == region_id, AlertDismissal.key == key)).one()
        if not exists:
            s.add(AlertDismissal(region_id=region_id, key=key, dismissed_at=datetime.now(timezone.utc).isoformat()))
            s.commit()


def refresh_dismissals() -> None:
    """Re-check every region so dismissals of alerts that have since cleared are dropped even when nobody
    opens the app in between (run by the scheduler with the forecast warm-up)."""
    with Session(engine) as s:
        ids = s.scalars(select(Region.id)).all()
    for rid in ids:
        try:
            current_alerts(rid)
        except Exception:
            log.warning("alerts: couldn't refresh dismissals for region %s", rid, exc_info=True)
