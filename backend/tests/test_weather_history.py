from datetime import date, datetime, timedelta, timezone

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

import app.main  # noqa: F401  (registers every model so create_all sees all tables)
from app.cameras import activity, router as cam_router
from app.cameras.models import Camera, CameraSighting
from app.core.database import Base, engine
from app.forecast import history
from app.forecast.models import WeatherHour
from app.regions.models import Region

CHI = activity.region_tz("America/Chicago")


@pytest.fixture()
def db():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    history._recent_at.clear()
    history._failed_at.clear()
    with Session(engine) as s:
        s.add(Region(id=1, name="r", lat=34.7, lon=-92.3, property_timezone="America/Chicago"))
        s.commit()


def store(hours: dict[str, dict], provisional=0):
    with Session(engine) as s:
        for h, vals in hours.items():
            s.add(WeatherHour(region_id=1, hour_utc=h, provisional=provisional, **vals))
        s.commit()


def stored():
    with Session(engine) as s:
        return {w.hour_utc: w for w in s.scalars(select(WeatherHour))}


# ---------- weather_series ----------

def test_by_date_gives_each_local_days_average_low_and_high(db):
    store({"2025-11-08T12:00Z": {"temp_f": 40.0}, "2025-11-08T20:00Z": {"temp_f": 60.0},   # 6 am, 2 pm Nov 8
           "2025-11-09T03:00Z": {"temp_f": 50.0}})                                         # 9 pm Nov 8 local
    out = history.weather_series(1, CHI, "date", "temp", date(2025, 11, 8), date(2025, 11, 9))
    assert out["unit"] == "°F"
    assert out["points"] == [{"key": "2025-11-08", "avg": 50.0, "low": 40.0, "high": 60.0},
                             {"key": "2025-11-09", "avg": None, "low": None, "high": None}]   # no data that day


def test_rain_by_date_is_the_days_total_with_its_wettest_hour(db):
    store({"2025-11-08T12:00Z": {"precip_in": 0.1}, "2025-11-08T13:00Z": {"precip_in": 0.25},
           "2025-11-08T14:00Z": {"precip_in": 0.0}})
    p = history.weather_series(1, CHI, "date", "rain", date(2025, 11, 8), date(2025, 11, 8))["points"][0]
    assert (p["avg"], p["peak"], p["low"], p["high"]) == (0.35, 0.25, None, None)


def test_by_hour_averages_each_local_hour_across_days_through_a_dst_change(db):
    # US DST ended 2025-11-02: 6 am local is 11:00 UTC before it and 12:00 UTC after it
    store({"2025-11-01T11:00Z": {"wind_mph": 4.0}, "2025-11-03T12:00Z": {"wind_mph": 10.0}})
    pts = history.weather_series(1, CHI, "hour", "wind", date(2025, 11, 1), date(2025, 11, 3))["points"]
    assert len(pts) == 24 and pts[6] == {"key": 6, "avg": 7.0, "low": 4.0, "high": 10.0}
    assert pts[7]["avg"] is None


# ---------- ensure_hours ----------

def hourly_json(start: date, days: int, temp=50.0):
    times = [(datetime(start.year, start.month, start.day) + timedelta(hours=i)).strftime("%Y-%m-%dT%H:%M")
             for i in range(24 * days)]
    n = len(times)
    return {"hourly": {"time": times, "temperature_2m": [temp] * n, "pressure_msl": [1013.0] * n,
                       "wind_speed_10m": [5.0] * n, "precipitation": [0.0] * n}}


@pytest.fixture()
def api(monkeypatch):
    calls = []

    def get_json(url):
        calls.append(url)
        q = httpx.URL(url).params
        if "archive" in url:
            a, b = date.fromisoformat(q["start_date"]), date.fromisoformat(q["end_date"])
            return hourly_json(a, (b - a).days + 1)
        today = datetime.now(timezone.utc).date()
        return hourly_json(today - timedelta(days=history.RECENT_DAYS), history.RECENT_DAYS + 1, temp=70.0)

    monkeypatch.setattr(history, "_get_json", get_json)
    return calls


def test_old_days_come_from_the_archive_once_and_are_stored(db, api):
    history.ensure_hours(1, date(2025, 11, 8), date(2025, 11, 9))
    assert len(api) == 1 and "archive" in api[0] and "start_date=2025-11-07&end_date=2025-11-10" in api[0]
    rows = stored()
    assert len(rows) == 4 * 24 and rows["2025-11-08T12:00Z"].pressure_inhg == round(1013 * history.HPA_TO_INHG, 3)
    history.ensure_hours(1, date(2025, 11, 8), date(2025, 11, 9))
    assert len(api) == 1                                                   # already stored: nothing fetched


def test_only_the_gap_is_fetched(db, api):
    store({f"2025-11-0{d}T{h:02d}:00Z": {"temp_f": 40.0} for d in (7, 8) for h in range(24)})
    history.ensure_hours(1, date(2025, 11, 8), date(2025, 11, 9))
    assert len(api) == 1 and "start_date=2025-11-09&end_date=2025-11-10" in api[0]


def test_recent_days_are_provisional_until_the_archive_has_them(db, api):
    today = datetime.now(timezone.utc).date()
    history.ensure_hours(1, today - timedelta(days=2), today)
    assert any("forecast" in u for u in api)
    recent = [w for w in stored().values() if w.hour_utc[:10] == (today - timedelta(days=2)).isoformat()]
    assert recent and all(w.provisional == 1 and w.temp_f == 70.0 for w in recent)
    assert all(datetime.fromisoformat(h.replace("Z", "+00:00")) <= datetime.now(timezone.utc) for h in stored())

    # once the day is old enough for the archive, its archive values replace the provisional ones
    history._upsert(1, history._parse(hourly_json(today - timedelta(days=2), 1), False, datetime.now(timezone.utc)))
    recent = [w for w in stored().values() if w.hour_utc[:10] == (today - timedelta(days=2)).isoformat()]
    assert all(w.provisional == 0 and w.temp_f == 50.0 for w in recent)
    # ...and a later provisional fetch never overwrites archive values
    history._upsert(1, history._parse(hourly_json(today - timedelta(days=2), 1, temp=99.0), True,
                                      datetime.now(timezone.utc)))
    assert all(w.temp_f == 50.0 for w in stored().values() if w.hour_utc[:10] == (today - timedelta(days=2)).isoformat())


def test_a_failed_fetch_is_not_retried_right_away_and_never_raises(db, monkeypatch):
    calls = []

    def boom(url):
        calls.append(url)
        raise httpx.ConnectError("down")
    monkeypatch.setattr(history, "_get_json", boom)
    history.ensure_hours(1, date(2025, 11, 8), date(2025, 11, 9))
    history.ensure_hours(1, date(2025, 11, 8), date(2025, 11, 9))
    assert len(calls) == 1 and stored() == {}


# ---------- endpoint ----------

def test_activity_carries_the_weather_series_only_when_asked(db, monkeypatch):
    monkeypatch.setattr(history, "ensure_hours", lambda *a: None)
    with Session(engine) as s:
        cam = Camera(region_id=1, name="A", brand="spypoint", is_active=1, is_deleted=0)
        s.add(cam)
        s.flush()
        s.add(CameraSighting(camera_id=cam.id, timestamp="2025-11-08T12:00:00Z", species="White-tailed Deer",
                             confidence_score=0.9))
        s.commit()
    store({"2025-11-08T12:00Z": {"temp_f": 45.0}})
    args = dict(brand=[], camera_id=[], species=[], date_from=None, date_to=date(2025, 11, 9), region_id=1, _=None)
    assert "weather" not in cam_router.camera_activity(**args)
    by_hour = cam_router.camera_activity(**args, weather="temp")["weather"]
    assert len(by_hour["points"]) == 24 and by_hour["points"][6]["avg"] == 45.0
    by_date = cam_router.camera_activity(**args, by="date", weather="temp")["weather"]
    assert [p["key"] for p in by_date["points"]] == ["2025-11-08", "2025-11-09"]
