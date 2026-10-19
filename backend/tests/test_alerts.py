"""Alerts bell: each camera / system / setup check fires on the condition and stays quiet otherwise, and
dismissals hide an alert until its condition clears."""
import json
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

import app.main  # noqa: F401  (imports every model so create_all sees all tables)
from app import scheduler as sch
from app.alerts import router as alerts_router
from app.alerts import service
from app.alerts.models import AlertDismissal
from app.cameras.models import Camera, CameraSighting, DeerDetection
from app.core.database import Base, engine
from app.deer_sign.models import DeerSign
from app.forecast import service as fc_service
from app.jobs.models import JobRun
from app.regions.models import Region
from app.settings.models import Setting
from app.stands.models import Stand

NOW = datetime.now(timezone.utc)
FULL_STAND = dict(downhill_deg=90.0, deer_approach_deg=270.0)


def iso(dt):
    return dt.isoformat()


class _Trigger:
    def __init__(self, minutes=None):
        self.interval = timedelta(minutes=minutes) if minutes else None


class _Job:
    def __init__(self, job_id, minutes=None):
        self.id, self.trigger = job_id, _Trigger(minutes)


class _Sched:
    def __init__(self, jobs):
        self._jobs = jobs

    def get_jobs(self):
        return self._jobs


@pytest.fixture()
def db(monkeypatch):
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        s.add(Region(id=1, name="R", lat=34.7, lon=-92.3, rut_peak_month=12, rut_peak_day=5,
                     property_timezone="America/Chicago", is_default=1, created_at=""))
        s.add(Stand(id=1, region_id=1, name="Ridge", lat=34.7, lon=-92.3, **FULL_STAND))
        s.commit()
    # a running scheduler with no jobs, terrain that's up to date, no buck model, no fetch history
    monkeypatch.setattr(sch, "_scheduler", _Sched([]))
    monkeypatch.setattr(sch, "_started_at", NOW)
    monkeypatch.setattr(service.region_terrain, "needs_refresh", lambda rid: False)
    monkeypatch.setattr(service.bucks_mod, "load_model", lambda: None)
    monkeypatch.setattr(fc_service, "_fetch_status", {})
    yield


def add(*rows):
    with Session(engine) as s:
        for r in rows:
            s.add(r)
        s.commit()


def cam(**kw):
    base = dict(region_id=1, name="Doe Cam", brand="spypoint", is_active=1, is_deleted=0, stand_id=1,
                last_seen_at=iso(NOW), created_at=iso(NOW - timedelta(days=30)))
    return Camera(**{**base, **kw})


def keys():
    return {a["key"].split(":")[0] for a in service.current_alerts(1)}


def test_a_healthy_setup_has_no_alerts(db):
    add(cam(battery_pct=80, signal_pct=90, sd_used_mb=1000, sd_size_mb=28000, photo_count=10, photo_limit=250))
    assert service.current_alerts(1) == []


@pytest.mark.parametrize("kw, key, severity", [
    (dict(last_seen_at=iso(NOW - timedelta(days=3))), "cam_offline", "warning"),
    (dict(photo_count=238, photo_limit=250), "cam_quota", "warning"),
    (dict(photo_count=250, photo_limit=250), "cam_quota", "critical"),
    (dict(battery_pct=18, power_type="AA"), "cam_battery", "warning"),
    (dict(battery_pct=8), "cam_battery", "critical"),
    (dict(battery_pct=40, battery_level="low"), "cam_battery", "warning"),
    (dict(signal_pct=20, signal_bars=1), "cam_signal", "warning"),
    (dict(signal_pct=80, low_signal=1), "cam_signal", "warning"),
    (dict(sd_used_mb=26500, sd_size_mb=28500), "cam_sd", "warning"),
    (dict(sd_used_mb=28400, sd_size_mb=28500), "cam_sd", "critical"),
])
def test_camera_checks(db, kw, key, severity):
    add(cam(**kw))
    alerts = service.current_alerts(1)
    hit = [a for a in alerts if a["key"].startswith(key)]
    assert len(hit) == 1 and hit[0]["severity"] == severity and hit[0]["camera_id"] and hit[0]["stand_id"] == 1


def test_battery_detail_names_the_power_source(db):
    add(cam(battery_pct=18, power_type="AA"))
    a = service.current_alerts(1)[0]
    assert a["title"] == "Doe Cam battery low" and a["detail"] == "18% (AA)"


def test_unassigned_camera_is_a_quiet_info_alert(db):
    add(cam(stand_id=None))
    a = service.current_alerts(1)[0]
    assert a["key"].startswith("cam_unassigned") and a["quiet"] and a["severity"] == "info"


def test_failing_sync_names_the_camera(db):
    add(cam(), JobRun(job_id="sync_cameras", region_id=None, trigger="scheduled", status="partial",
                      started_at=iso(NOW), finished_at=iso(NOW), done=1, failed=1,
                      errors_json=json.dumps([{"item": "Doe Cam", "error": "SpyPoint login failed (401)"}])))
    alerts = service.current_alerts(1)
    sync = next(a for a in alerts if a["key"].startswith("cam_sync"))
    assert sync["detail"] == "SpyPoint login failed (401)"
    assert any(a["key"].startswith("job:") and a["severity"] == "warning" for a in alerts)


def test_recent_buck_photo_alerts_once_per_camera(db):
    add(cam(id=5))
    add(CameraSighting(id=1, stand_id=1, camera_id=5, timestamp=iso(NOW - timedelta(hours=3)), species="white-tailed deer"),
        CameraSighting(id=2, stand_id=1, camera_id=5, timestamp=iso(NOW - timedelta(days=5)), species="white-tailed deer"))
    add(DeerDetection(id=10, sighting_id=1, box_json="[0,0,1,1]", label="buck"),
        DeerDetection(id=11, sighting_id=2, box_json="[0,0,1,1]", label="buck"))   # too old
    bucks = [a for a in service.current_alerts(1) if a["key"].startswith("buck:")]
    assert len(bucks) == 1 and bucks[0]["key"] == "buck:5:10" and "1 buck photo" in bucks[0]["detail"]


def _noon(days_ago):
    # 12:00 local (CST/CDT) days ago: always daylight
    d = (NOW - timedelta(days=days_ago)).date()
    return datetime(d.year, d.month, d.day, 18, 0, tzinfo=timezone.utc)


def test_daylight_deer_spike(db):
    add(cam(id=5))
    sightings = [CameraSighting(stand_id=1, camera_id=5, timestamp=iso(_noon(0) if _noon(0) < NOW else _noon(1)),
                                species="white-tailed deer") for _ in range(4)]
    sightings.append(CameraSighting(stand_id=1, camera_id=5, timestamp=iso(_noon(10)), species="white-tailed deer"))
    add(*sightings)
    spike = [a for a in service.current_alerts(1) if a["key"].startswith("deer_spike")]
    assert len(spike) == 1 and spike[0]["stand_id"] == 1 and spike[0]["title"] == "More deer at Ridge"


def test_no_spike_when_deer_are_usual(db):
    add(cam(id=5))
    add(*[CameraSighting(stand_id=1, camera_id=5, timestamp=iso(_noon(d)), species="white-tailed deer")
          for d in range(3, 29) for _ in range(2)])   # ~4 per 48 h usually
    add(*[CameraSighting(stand_id=1, camera_id=5, timestamp=iso(_noon(1)), species="white-tailed deer")
          for _ in range(3)])
    assert "deer_spike" not in keys()


def test_failed_job_and_late_job(db, monkeypatch):
    add(JobRun(job_id="weather_history", region_id=None, trigger="scheduled", status="failed",
               started_at=iso(NOW), finished_at=iso(NOW), message="archive unreachable"))
    monkeypatch.setattr(sch, "_started_at", NOW - timedelta(hours=5))
    monkeypatch.setattr(sch, "_scheduler", _Sched([_Job("sync_cameras", minutes=30), _Job("auto_cleanup")]))
    add(JobRun(job_id="sync_cameras", region_id=None, trigger="scheduled", status="success",
               started_at=iso(NOW - timedelta(hours=3)), finished_at=iso(NOW - timedelta(hours=3))))
    alerts = service.current_alerts(1)
    failed = next(a for a in alerts if a["key"].startswith("job:"))
    assert failed["severity"] == "critical" and failed["detail"] == "archive unreachable"
    late = {a["key"] for a in alerts if a["key"].startswith("job_late")}
    assert late == {"job_late:sync_cameras"}   # the daily job isn't due yet


def test_no_scheduler_is_critical(db, monkeypatch):
    monkeypatch.setattr(sch, "_scheduler", None)
    a = next(a for a in service.current_alerts(1) if a["key"] == "scheduler_down")
    assert a["severity"] == "critical"


def test_weather_provider_failing(db, monkeypatch):
    monkeypatch.setattr(fc_service, "_fetch_status", {(34.7, -92.3): {
        "provider": "Open-Meteo", "ok_at": NOW.timestamp() - 7200, "error_at": NOW.timestamp(), "error": "timed out"}})
    a = next(a for a in service.current_alerts(1) if a["key"] == "weather_provider")
    assert a["title"] == "Open-Meteo isn't responding" and "timed out" in a["detail"]
    monkeypatch.setattr(fc_service, "_fetch_status", {(34.7, -92.3): {
        "provider": "Open-Meteo", "ok_at": NOW.timestamp(), "error_at": NOW.timestamp() - 60, "error": "x"}})
    assert "weather_provider" not in keys()


def test_stand_setup_gaps_and_property_terrain(db, monkeypatch):
    add(Stand(id=2, region_id=1, name="Bare", lat=34.701, lon=-92.301))
    monkeypatch.setattr(service.region_terrain, "needs_refresh", lambda rid: True)
    monkeypatch.setattr(service.region_terrain, "load_grid", lambda rid: None)
    alerts = service.current_alerts(1)
    got = {a["key"] for a in alerts}
    assert {"stand_terrain:2", "stand_approach:2", "property_terrain"} <= got
    assert "stand_terrain:1" not in got and "stand_approach:1" not in got
    assert next(a for a in alerts if a["key"] == "property_terrain")["title"] == "Property terrain hasn't been analyzed"


def test_stale_sign(db):
    add(DeerSign(region_id=1, kind="scrape", name="old", lat=34.7, lon=-92.3, is_active=1,
                 created_at=iso(NOW - timedelta(days=60))),
        DeerSign(region_id=1, kind="scrape", name="new", lat=34.7, lon=-92.3, is_active=1, created_at=iso(NOW)))
    a = next(a for a in service.current_alerts(1) if a["key"].startswith("stale_sign"))
    assert a["title"] == "1 scrape getting stale"


def test_buck_model_off(db, monkeypatch):
    monkeypatch.setattr(service.bucks_mod, "load_model",
                        lambda: {"enabled": False, "cv_accuracy": 0.71, "w": [0.1], "trained_at": "t1"})
    monkeypatch.setattr(service.bucks_mod, "min_accuracy", lambda: 0.85)
    a = next(a for a in service.current_alerts(1) if a["key"] == "buck_model:t1")
    assert "71%" in a["detail"] and "85%" in a["detail"]


def test_dismiss_hides_until_the_condition_clears_and_returns(db):
    add(cam(id=5, battery_pct=15))
    service.dismiss(1, "cam_battery:5")
    service.dismiss(1, "cam_battery:5")   # twice is fine
    assert "cam_battery" not in keys()
    with Session(engine) as s:
        s.get(Camera, 5).battery_pct = 90      # batteries changed
        s.commit()
    assert "cam_battery" not in keys()
    with Session(engine) as s:
        assert s.scalars(select(AlertDismissal)).all() == []   # the cleared dismissal is gone
        s.get(Camera, 5).battery_pct = 12      # and running low again
        s.commit()
    assert "cam_battery" in keys()


def test_alerts_sort_critical_first(db):
    add(cam(id=5, battery_pct=5, stand_id=None), cam(id=6, name="B", signal_pct=10))
    sev = [a["severity"] for a in service.current_alerts(1)]
    assert sev == sorted(sev, key=service.SEVERITY_ORDER.get)


def test_a_failing_check_doesnt_hide_the_others(db, monkeypatch):
    add(cam(battery_pct=5))
    monkeypatch.setattr(service, "_job_alerts", lambda *a: 1 / 0)
    assert "cam_battery" in keys()


# ---------- alert types: grouped, and each can be turned off in Settings ----------

def _turn_off(*types):
    with Session(engine) as s:
        row = s.get(Setting, "alerts_off")
        if row:
            row.value = ",".join(types)
        else:
            s.add(Setting(key="alerts_off", value=",".join(types)))
        s.commit()


def test_alert_types_are_unique_and_grouped():
    ids = [t["id"] for t in service.ALERT_TYPES]
    groups = {g["id"] for g in service.ALERT_GROUPS}
    assert len(ids) == len(set(ids)) and {t["group"] for t in service.ALERT_TYPES} == groups
    assert all(t["label"] and t["description"] for t in service.ALERT_TYPES)
    out = alerts_router.alert_types(_=None)
    assert out["groups"] == service.ALERT_GROUPS and out["types"] == service.ALERT_TYPES


def test_every_alert_has_a_registered_type_in_its_group(db, monkeypatch):
    monkeypatch.setattr(sch, "_scheduler", None)
    monkeypatch.setattr(service.region_terrain, "needs_refresh", lambda rid: True)
    monkeypatch.setattr(service.region_terrain, "load_grid", lambda rid: None)
    with Session(engine) as s:
        st = s.get(Stand, 1)
        st.deer_approach_deg = None
        s.commit()
    add(cam(id=5, battery_pct=5, signal_pct=10, stand_id=None))
    add(CameraSighting(id=1, stand_id=1, camera_id=5, timestamp=iso(NOW - timedelta(hours=3)), species="white-tailed deer"))
    add(DeerDetection(id=10, sighting_id=1, box_json="[0,0,1,1]", label="buck"))
    group_of = {t["id"]: t["group"] for t in service.ALERT_TYPES}
    alerts = service.current_alerts(1)
    assert len(alerts) >= 5
    for a in alerts:
        assert a["category"] == group_of[service.alert_type(a["key"])], a["key"]
    assert any(a["category"] == "deer" for a in alerts) and any(a["category"] == "setup" for a in alerts)


def test_turned_off_types_are_left_out(db):
    add(cam(id=5, battery_pct=15, signal_pct=10))
    assert {"cam_battery", "cam_signal"} <= keys()
    _turn_off("cam_battery", "deer_spike")
    assert "cam_battery" not in keys() and "cam_signal" in keys()
    _turn_off()
    assert "cam_battery" in keys()


def test_turning_a_type_off_and_on_keeps_its_dismissals(db):
    add(cam(id=5, battery_pct=15))
    service.dismiss(1, "cam_battery:5")
    _turn_off("cam_battery")
    assert "cam_battery" not in keys()
    with Session(engine) as s:
        assert len(s.scalars(select(AlertDismissal)).all()) == 1   # still there while the type is off
    _turn_off()
    assert "cam_battery" not in keys()                             # still dismissed
