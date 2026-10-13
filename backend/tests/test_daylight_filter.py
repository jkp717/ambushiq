from datetime import date, timedelta

import pytest
from sqlalchemy.orm import Session

import app.main  # noqa: F401  (registers every model so create_all sees all tables)
from app.cameras import activity, router as cam_router
from app.cameras.models import Camera, CameraSighting
from app.core.database import Base, engine
from app.forecast.providers import _sun_times_utc
from app.regions.models import Region

LAT, LON, TZ = 34.7, -92.3, "America/Chicago"      # Little Rock
DAY = date(2025, 11, 8)
SUNRISE, SUNSET = _sun_times_utc(LAT, LON, DAY, TZ)
MIN = timedelta(minutes=1)


def test_shooting_light_runs_30_minutes_either_side_of_the_sun():
    light = activity.ShootingLight(LAT, LON, TZ)
    assert light(SUNRISE - 25 * MIN) and not light(SUNRISE - 40 * MIN)
    assert light(SUNRISE + (SUNSET - SUNRISE) / 2)                        # midday
    assert light(SUNSET + 25 * MIN) and not light(SUNSET + 40 * MIN)
    assert not light(SUNRISE - timedelta(hours=5))                         # ~2 am


def test_an_evening_photo_on_the_next_utc_day_uses_its_local_dates_sunset():
    light = activity.ShootingLight(LAT, LON, TZ)
    assert SUNSET.date() == DAY and (SUNSET + 2 * timedelta(hours=1)).date() > DAY  # 7 pm local is the next UTC day
    assert light(SUNSET + 20 * MIN) and not light(SUNSET + timedelta(hours=2))


def test_polar_days_hide_nothing():
    light = activity.ShootingLight(89.9, 0.0, "UTC")
    assert light(SUNRISE) and light(SUNRISE + timedelta(hours=12))


@pytest.fixture()
def db():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        s.add(Region(id=1, name="r", lat=LAT, lon=LON, property_timezone=TZ))
        cam = Camera(region_id=1, name="A", brand="spypoint", is_active=1, is_deleted=0)
        s.add(cam)
        s.flush()
        for t in (SUNRISE - 20 * MIN, SUNRISE - timedelta(hours=5)):             # dawn, and night
            s.add(CameraSighting(camera_id=cam.id, timestamp=t.strftime("%Y-%m-%dT%H:%M:%SZ"),
                                 species="White-tailed Deer", confidence_score=0.9, is_animal=1))
        s.commit()


def gallery(**kw):
    args = dict(brand=[], camera_id=[], species=[], date_from=None, date_to=None, hour_from=None, hour_to=None,
                include_empty=False, favorites=False, bucks=False, daylight=False, before_ts=None, before_id=None,
                limit=48, region_id=1, _=None)
    return cam_router.camera_gallery(**{**args, **kw})["items"]


def activity_total(**kw):
    args = dict(brand=[], camera_id=[], species=[], date_from=DAY - timedelta(days=1), date_to=DAY, bucks=False,
                daylight=False, by="date", weather=None, region_id=1, _=None)
    return cam_router.camera_activity(**{**args, **kw})["total"]


def test_gallery_daylight_keeps_dawn_and_drops_night(db):
    assert len(gallery()) == 2
    kept = gallery(daylight=True)
    assert len(kept) == 1 and activity.parse_utc(kept[0]["timestamp"]) == (SUNRISE - 20 * MIN).replace(microsecond=0)


def test_activity_daylight_counts_only_shooting_light(db):
    assert activity_total() == 2
    assert activity_total(daylight=True) == 1
