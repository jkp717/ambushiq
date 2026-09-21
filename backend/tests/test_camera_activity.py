from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy.orm import Session

import app.main  # noqa: F401  (registers every model so create_all sees all tables)
from app.cameras import activity, router as cam_router
from app.cameras.models import Camera, CameraSighting
from app.core.database import Base, engine
from app.regions.models import Region

CHI = activity.region_tz("America/Chicago")


def utc(y, m, d, h=0, mi=0):
    return datetime(y, m, d, h, mi, tzinfo=timezone.utc)


def rows(*times, cam=1, sp="White-tailed Deer"):
    return [(cam, sp, t) for t in times]


# ---------- pure logic ----------

def test_photos_in_one_visit_collapse_to_a_single_sighting():
    t = utc(2025, 11, 8, 12, 0)
    burst = rows(t, t + timedelta(minutes=3), t + timedelta(minutes=6))       # gaps of 3 min chain together
    later = rows(t + timedelta(minutes=30))
    assert len(activity.collapse_bursts(burst + later)) == 2
    other_species = [(1, "Coyote", t + timedelta(minutes=1))]
    other_camera = [(2, "White-tailed Deer", t + timedelta(minutes=1))]
    assert len(activity.collapse_bursts(burst + other_species + other_camera)) == 3


def test_average_divides_by_every_day_in_range_including_quiet_days():
    data = rows(utc(2025, 11, 8, 12, 0), utc(2025, 11, 9, 12, 10), utc(2025, 11, 9, 13, 30))   # 6:00, 6:10, 7:30 local
    out = activity.hourly_activity(data, tz=CHI, today=date(2025, 11, 10), date_from=None, date_to=None,
                                   recorded_since=date(2025, 11, 8))
    assert out["days"] == 3 and out["total"] == 3                              # Nov 8, 9, 10 (10th had nothing)
    assert out["hours"][6] == {"hour": 6, "avg": round(2 / 3, 3), "total": 2}
    assert out["hours"][7]["total"] == 1 and out["hours"][8]["total"] == 0


def test_average_uses_only_the_selected_range():
    data = rows(utc(2025, 11, 8, 12, 0), utc(2025, 11, 9, 12, 0), utc(2025, 11, 10, 12, 0))
    out = activity.hourly_activity(data, tz=CHI, today=date(2025, 11, 10), date_from=date(2025, 11, 9),
                                   date_to=None, recorded_since=date(2025, 11, 8))
    assert out["days"] == 2 and out["total"] == 2 and out["date_from"] == "2025-11-09"
    assert out["hours"][6]["avg"] == 1.0                                       # 2 sightings / 2 days


def test_range_is_clamped_to_when_cameras_were_recording_and_to_today():
    data = rows(utc(2025, 11, 9, 12, 0))
    out = activity.hourly_activity(data, tz=CHI, today=date(2025, 11, 10), date_from=date(2025, 10, 1),
                                   date_to=date(2026, 1, 1), recorded_since=date(2025, 11, 9))
    assert (out["date_from"], out["date_to"], out["days"]) == ("2025-11-09", "2025-11-10", 2)


def test_empty_and_inverted_ranges_do_not_divide_by_zero():
    for kwargs in ({"recorded_since": None},
                   {"recorded_since": date(2025, 11, 9), "date_from": date(2025, 11, 12)}):
        out = activity.hourly_activity([], tz=CHI, today=date(2025, 11, 10),
                                       **{"date_from": None, "date_to": None, **kwargs})
        assert out["days"] == 0 and out["total"] == 0 and len(out["hours"]) == 24


def test_hours_are_property_local_across_a_dst_change():
    # US DST ended 2025-11-02: noon UTC is 7 am CDT before it and 6 am CST after it.
    data = rows(utc(2025, 11, 1, 12, 0), utc(2025, 11, 3, 12, 0))
    out = activity.hourly_activity(data, tz=CHI, today=date(2025, 11, 3), date_from=None, date_to=None,
                                   recorded_since=date(2025, 11, 1))
    assert out["hours"][7]["total"] == 1 and out["hours"][6]["total"] == 1


def test_a_late_evening_photo_counts_on_its_local_day_not_the_utc_day():
    data = rows(utc(2025, 11, 9, 3, 30))                                       # 9:30 pm on Nov 8 in Chicago
    out = activity.hourly_activity(data, tz=CHI, today=date(2025, 11, 9), date_from=date(2025, 11, 8),
                                   date_to=date(2025, 11, 8), recorded_since=date(2025, 11, 8))
    assert out["hours"][21]["total"] == 1 and out["days"] == 1


def test_time_of_day_window_including_midnight_wrap():
    at = lambda h, m=0: datetime(2025, 11, 8, h, m, tzinfo=CHI)
    assert activity.in_hour_window(at(6), 6, 9) and activity.in_hour_window(at(8, 59), 6, 9)
    assert not activity.in_hour_window(at(9), 6, 9)                            # end is exclusive
    assert activity.in_hour_window(at(23), 20, 5) and activity.in_hour_window(at(4, 30), 20, 5)
    assert not activity.in_hour_window(at(12), 20, 5)
    assert activity.in_hour_window(at(23, 59), 18, 24)                         # 24 = end of day
    assert activity.in_hour_window(at(3), None, None) and activity.in_hour_window(at(3), 5, 5)


def test_species_matching():
    assert activity.species_matches("Coyote", []) and activity.species_matches(None, [])
    assert activity.species_matches(None, [activity.UNCLASSIFIED])
    assert not activity.species_matches(None, ["Coyote"])
    assert activity.species_matches("Coyote", ["Coyote", activity.UNCLASSIFIED])
    assert not activity.species_matches("Raccoon", ["Coyote"])


# ---------- endpoints (real router functions, in-memory SQLite) ----------

@pytest.fixture()
def cams():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    ids = {}
    with Session(engine) as s:
        s.add(Region(id=1, name="r1", lat=34.7, lon=-92.3, property_timezone="America/Chicago"))
        s.add(Region(id=2, name="r2", lat=34.7, lon=-92.3, property_timezone="America/Chicago"))
        for key, region, brand, name, deleted in (("a", 1, "spypoint", "North Field", 0), ("b", 1, "reveal", "Creek", 0),
                                                   ("c", 2, "spypoint", "Other Region", 0), ("d", 1, "spypoint", "Gone", 1)):
            cam = Camera(region_id=region, name=name, brand=brand, is_active=1, is_deleted=deleted)
            s.add(cam)
            s.flush()
            ids[key] = cam.id

        def add(cam, ts, species):
            s.add(CameraSighting(camera_id=ids[cam], timestamp=ts, species=species, confidence_score=0.9))
        add("a", "2025-11-08T12:00:00Z", "White-tailed Deer")     # 6:00 am local
        add("a", "2025-11-08T12:02:00Z", "White-tailed Deer")     # same visit
        add("a", "2025-11-09T12:00:00Z", "Coyote")                # 6:00 am
        add("a", "2025-11-09T03:00:00Z", None)                    # 9:00 pm Nov 8 local, unclassified
        add("b", "2025-11-09T23:30:00Z", "White-tailed Deer")     # 5:30 pm
        add("c", "2025-11-09T12:00:00Z", "White-tailed Deer")     # other region: must never appear
        add("d", "2025-11-09T12:00:00Z", "White-tailed Deer")     # deleted camera: must never appear
        s.commit()
    return ids


def act(**kw):
    args = dict(brand=[], camera_id=[], species=[], date_from=None, date_to=date(2025, 11, 10), region_id=1, _=None)
    return cam_router.camera_activity(**{**args, **kw})


def gallery(**kw):
    args = dict(brand=[], camera_id=[], species=[], date_from=None, date_to=None, hour_from=None, hour_to=None,
                before_ts=None, before_id=None, limit=48, region_id=1, _=None)
    return cam_router.camera_gallery(**{**args, **kw})


def test_activity_counts_visits_and_ignores_other_regions_and_deleted_cameras(cams):
    out = act()
    assert out["total"] == 4                                   # deer visit (2 photos -> 1), coyote, unclassified, reveal deer
    assert out["days"] == 3 and (out["date_from"], out["date_to"]) == ("2025-11-08", "2025-11-10")
    assert out["hours"][6]["total"] == 2 and out["hours"][21]["total"] == 1 and out["hours"][17]["total"] == 1


def test_activity_brand_and_camera_filters(cams):
    assert act(brand=["reveal"])["total"] == 1
    assert act(camera_id=[cams["a"]])["total"] == 3
    assert act(brand=["spypoint"], camera_id=[cams["b"]])["total"] == 0        # contradictory filters -> nothing


def test_activity_species_filter_including_unclassified(cams):
    assert act(species=["White-tailed Deer"])["total"] == 2
    assert act(species=["Coyote", activity.UNCLASSIFIED])["total"] == 2


def test_activity_days_follow_the_date_filter_not_the_whole_history(cams):
    out = act(date_from=date(2025, 11, 9))
    assert out["days"] == 2 and out["total"] == 2                              # coyote + reveal deer (the 9 pm photo is Nov 8 local)
    assert out["hours"][6]["avg"] == 0.5                                       # coyote / 2 days


def test_filter_options_list_species_present_in_this_region_only(cams):
    out = cam_router.camera_filter_options(region_id=1, _=None)
    assert out == {"species": ["Coyote", "White-tailed Deer"], "has_unclassified": True}


def test_gallery_is_newest_first_and_pages_with_a_cursor(cams):
    first = gallery(limit=2)
    stamps = [i["timestamp"] for i in first["items"]]
    assert stamps == sorted(stamps, reverse=True) and len(stamps) == 2 and first["next"]
    seen = list(first["items"])
    nxt = first["next"]
    while nxt:
        page = gallery(limit=2, before_ts=nxt["ts"], before_id=nxt["id"])
        seen += page["items"]
        nxt = page["next"]
    assert len(seen) == 5 and len({i["id"] for i in seen}) == 5                # every photo once, none from region 2 / deleted
    assert {i["camera_name"] for i in seen} == {"North Field", "Creek"}


def test_gallery_filters(cams):
    assert {i["camera_brand"] for i in gallery(brand=["reveal"])["items"]} == {"reveal"}
    assert len(gallery(species=[activity.UNCLASSIFIED])["items"]) == 1
    assert len(gallery(species=["Coyote"], camera_id=[cams["a"]])["items"]) == 1
    only_8th = gallery(date_from=date(2025, 11, 8), date_to=date(2025, 11, 8))["items"]
    assert len(only_8th) == 3                                                  # two 6 am deer photos + the 9 pm unclassified one
    morning = gallery(hour_from=5, hour_to=9)["items"]
    assert len(morning) == 3 and all("T12:0" in i["timestamp"] for i in morning)
    night = gallery(hour_from=20, hour_to=5)["items"]                          # wraps midnight
    assert [i["species"] for i in night] == [None]


def test_gallery_with_no_matches_returns_an_empty_page(cams):
    assert gallery(species=["Raccoon"]) == {"items": [], "next": None}
