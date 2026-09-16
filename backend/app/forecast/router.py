"""Route handlers for forecast, wind/thermal ranking, and the map/day views."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.cameras import detection as detection_mod
from app.cameras.models import CameraSighting
from app.core.database import engine
from app.corridors.models import Corridor
from app.deer_sign.models import DeerSign
from app.dependencies import get_active_region_id, require_token
from app.forecast import scoring
from app.forecast.providers import weather_provider_meta
from app.forecast.schemas import DayRankIn, HourRankIn, ManualRankIn, SitRankIn
from app.forecast.service import (
    _camera_health,
    _camera_ready,
    _camera_status_by_stand,
    _temp_swing_by_day,
    build_sits,
    get_forecast,
    proximity_bonus,
)
from app.regions.service import get_region_dict
from app.settings.service import _thermal_params, get_settings
from app.stands.models import Stand
from app.zones.models import Zone

router = APIRouter(tags=["forecast"])


@router.get("/api/weather-providers")
def weather_providers(_=Depends(require_token)):
    """Provider metadata for the settings weather-source dropdown."""
    return {"providers": weather_provider_meta()}


@router.get("/api/forecast")
async def forecast_endpoint(region_id: int = Depends(get_active_region_id), _=Depends(require_token)):
    region = get_region_dict(region_id)
    with Session(engine) as s:
        first = s.scalars(select(Stand).where(Stand.region_id == region_id).order_by(Stand.name)).first()
        if not first:
            raise HTTPException(400, "add a stand first")
        lat, lon = first.lat, first.lon
    try:
        fc = await get_forecast(lat, lon, tz_name=region["property_timezone"])
    except Exception as e:
        raise HTTPException(502, f"forecast unreachable: {e}")
    return {"sits": build_sits(fc)}


@router.post("/api/rank/sit")
async def rank_sit(body: SitRankIn, region_id: int = Depends(get_active_region_id), _=Depends(require_token)):
    region = get_region_dict(region_id)
    with Session(engine) as s:
        stands = [r.to_dict() for r in s.scalars(
            select(Stand).where(Stand.is_active == 1, Stand.region_id == region_id)).all()]
        first = s.scalars(select(Stand).where(Stand.region_id == region_id).order_by(Stand.name)).first()
        if not first:
            raise HTTPException(400, "no stands")
        lat, lon = first.lat, first.lon
    fc = await get_forecast(lat, lon, tz_name=region["property_timezone"])
    h = fc["hourly"]
    temp_swing_by_day = _temp_swing_by_day(h)
    mid = (body.sunrise_h + body.sunset_h) / 2
    tp = _thermal_params(get_settings())
    results = []
    for st in stands:
        agg, n, sample = 0.0, 0, None
        for i in body.sit_idxs:
            hour = {
                "wind_dir": h["wind_direction_10m"][i], "wind_speed": h["wind_speed_10m"][i],
                "gust": h["wind_gusts_10m"][i], "solar": h["shortwave_radiation"][i],
                "time_h": datetime.fromisoformat(h["time"][i]).hour,
                "sunrise_h": body.sunrise_h, "sunset_h": body.sunset_h,
                "temp_swing": temp_swing_by_day.get(h["time"][i][:10], 0.0),
            }
            sc = scoring.score_stand_hour(st, hour, tp)
            agg += sc["total"]
            n += 1
            dist = abs(hour["time_h"] - mid)
            if sample is None or dist < sample["dist"]:
                sample = {"hour": hour, "score": sc, "dist": dist}
        results.append({"stand": st, "avg": round(agg / max(1, n), 3), "sample": sample})
    results.sort(key=lambda x: x["avg"], reverse=True)
    return {"ranked": results}


@router.post("/api/rank/manual")
def rank_manual(body: ManualRankIn, region_id: int = Depends(get_active_region_id), _=Depends(require_token)):
    with Session(engine) as s:
        stands = [r.to_dict() for r in s.scalars(
            select(Stand).where(Stand.is_active == 1, Stand.region_id == region_id)).all()]
    wind_from = scoring.compass_to_deg(body.wind_dir)
    time_h = {"morning": 7, "midday": 13, "evening": 18}.get(body.period, 13)
    solar = 500 if body.period == "midday" else 50
    tp = _thermal_params(get_settings())
    results = []
    for st in stands:
        hour = {"wind_dir": wind_from, "wind_speed": body.wind_speed, "gust": body.gust,
                "solar": solar, "time_h": time_h, "sunrise_h": 6.5, "sunset_h": 19}
        sc = scoring.score_stand_hour(st, hour, tp)
        results.append({"stand": st, "avg": sc["total"], "sample": {"hour": hour, "score": sc}})
    results.sort(key=lambda x: x["avg"], reverse=True)
    return {"ranked": results}


@router.get("/api/hours")
async def list_hours(region_id: int = Depends(get_active_region_id), _=Depends(require_token)):
    """Forecast hours grouped by day, for the day picker + hourly slider."""
    region = get_region_dict(region_id)
    with Session(engine) as s:
        first = s.scalars(select(Stand).where(Stand.region_id == region_id).order_by(Stand.name)).first()
        if not first:
            raise HTTPException(400, "add a stand first")
        lat, lon = first.lat, first.lon
    try:
        fc = await get_forecast(lat, lon, days=14, tz_name=region["property_timezone"])
    except Exception as e:
        raise HTTPException(502, f"forecast unreachable: {e}")
    times = fc["hourly"]["time"]
    sun = fc["daily"]
    sun_by_day = {}
    for i in range(len(sun["sunrise"])):
        sr = datetime.fromisoformat(sun["sunrise"][i])
        ss = datetime.fromisoformat(sun["sunset"][i])
        sun_by_day[sun["sunrise"][i][:10]] = {
            "sunrise_h": sr.hour + sr.minute / 60,
            "sunset_h": ss.hour + ss.minute / 60,
            "sunrise": sun["sunrise"][i][11:16],
            "sunset": sun["sunset"][i][11:16],
        }
    days = {}
    for idx, tstr in enumerate(times):
        day = tstr[:10]
        days.setdefault(day, {"day": day,
                              "label": datetime.fromisoformat(day + "T12:00").strftime("%a %b %-d"),
                              **sun_by_day.get(day, {"sunrise_h": 6.5, "sunset_h": 19, "sunrise": "", "sunset": ""}),
                              "hours": []})
        dt = datetime.fromisoformat(tstr)
        days[day]["hours"].append({"index": idx, "hour": dt.hour,
                                   "label": dt.strftime("%-I %p").lower()})
    from datetime import date as _date
    # Derive the property's local "today" from the forecast's UTC offset so that
    # confidence flags stay correct during the UTC-to-local-midnight gap (up to 8h
    # wide for US timezones) when the server's UTC clock is already "tomorrow".
    utc_offset = int(fc.get("utc_offset_seconds", 0))
    local_today = (datetime.now(timezone.utc) + timedelta(seconds=utc_offset)).date()
    day_list = list(days.values())
    for dd in day_list:
        y, m, dnum = (int(x) for x in dd["day"].split("-"))
        days_out = (_date(y, m, dnum) - local_today).days
        dd["confidence"] = "high" if days_out <= 7 else "low"
        dd["days_out"] = days_out
    return {"days": day_list, "utc_offset_seconds": utc_offset}


@router.post("/api/map/conditions")
async def map_conditions(body: HourRankIn, region_id: int = Depends(get_active_region_id),
                          _=Depends(require_token)):
    """Per-stand wind + thermal vectors at one forecast hour, plus a ranked list
    in sync with that same hour. Drives the map indicators and the list together.
    Camera boost is applied when configured so the map rank matches /api/day/ranked."""
    region = get_region_dict(region_id)
    with Session(engine) as s:
        stands = [r.to_dict() for r in s.scalars(
            select(Stand).where(Stand.is_active == 1, Stand.region_id == region_id)).all()]
        first = s.scalars(select(Stand).where(Stand.region_id == region_id).order_by(Stand.name)).first()
        if not first:
            raise HTTPException(400, "no stands")
        lat, lon = first.lat, first.lon
    fc = await get_forecast(lat, lon, days=14, tz_name=region["property_timezone"])
    h = fc["hourly"]
    temp_swing_by_day = _temp_swing_by_day(h)
    i = body.time_index
    if i < 0 or i >= len(h["time"]):
        raise HTTPException(400, "time_index out of range")
    day = h["time"][i][:10]
    sun = fc["daily"]
    sr_h, ss_h = 6.5, 19.0
    for k in range(len(sun["sunrise"])):
        if sun["sunrise"][k][:10] == day:
            sr = datetime.fromisoformat(sun["sunrise"][k])
            ss = datetime.fromisoformat(sun["sunset"][k])
            sr_h = sr.hour + sr.minute / 60
            ss_h = ss.hour + ss.minute / 60
    hour = {
        "wind_dir": h["wind_direction_10m"][i], "wind_speed": h["wind_speed_10m"][i],
        "gust": h["wind_gusts_10m"][i], "solar": h["shortwave_radiation"][i],
        "time_h": datetime.fromisoformat(h["time"][i]).hour,
        "sunrise_h": sr_h, "sunset_h": ss_h,
        "temp_swing": temp_swing_by_day.get(day, 0.0),
    }

    settings = get_settings()
    max_cam_boost = float(settings.get("max_camera_boost_pct", 0.0) or 0.0)
    max_cam_penalty = float(settings.get("max_camera_penalty_pct", 0.0) or 0.0)
    cam_lookback = float(settings.get("camera_lookback_hours", 72.0) or 72.0)
    cam_health_max_age = float(settings.get("camera_health_max_age_hours", 48.0) or 48.0)
    cam_saturation = float(settings.get("camera_boost_saturation", 0.0)
                            or scoring.CAMERA_BOOST_SATURATION_DEFAULT)
    utc_offset = int(fc.get("utc_offset_seconds", 0))
    period = scoring.period_for_hour(hour["time_h"])
    tp = _thermal_params(settings)

    # Load recent sightings + camera-presence per stand when camera scoring is
    # configured — keeps map rank in sync with /api/day/ranked. Requires
    # species classification to actually be running (megadetector mode) —
    # in fallback mode species is always unknown, so boost/penalty can't work.
    camera_enabled = bool(period and (max_cam_boost or max_cam_penalty)
                           and detection_mod.species_available())
    sightings_by_stand: dict[int, list] = {}
    camera_status_by_stand: dict[int, dict] = {}
    if camera_enabled:
        camera_status_by_stand = _camera_status_by_stand(region_id)
        with Session(engine) as s:
            for row in s.scalars(select(CameraSighting)).all():
                sightings_by_stand.setdefault(row.stand_id, []).append(
                    {"timestamp": row.timestamp, "confidence_score": row.confidence_score,
                     "species": row.species})

    items = []
    for st in stands:
        vec = scoring.stand_hour_vectors(st, hour, tp)
        if camera_enabled:
            sid = st["id"]
            cam_info = camera_status_by_stand.get(sid)
            if cam_info:
                sightings = sightings_by_stand.get(sid, [])
                health = _camera_health(cam_info["last_seen_at"], cam_info["photo_count"],
                                         cam_info["photo_limit"], cam_health_max_age)
                boost = scoring.camera_boost(
                    period, sightings, max_cam_boost, utc_offset,
                    has_camera=True, max_penalty_pct=max_cam_penalty,
                    lookback_hours=cam_lookback,
                    camera_ready=_camera_ready(cam_info["created_at"], cam_lookback),
                    camera_healthy=health["healthy"], unhealthy_reason=health["reason"],
                    saturation=cam_saturation)
                vec = dict(vec)  # don't mutate the original
                vec["total"] = round(vec["total"] * boost["multiplier"], 3)
                vec["camera_boost"] = boost
        items.append({"stand": st, "vectors": vec})

    ranked = sorted(
        [{"stand": it["stand"], "avg": it["vectors"]["total"],
          "sample": {"hour": hour, "score": {
              "scent_to_deg": it["vectors"]["scent_to_deg"],
              "scent_score": it["vectors"]["scent_score"],
              "thermal_phase": it["vectors"]["thermal_phase"],
              "drainage_deg": it["vectors"]["thermal_to_deg"],
          }}} for it in items],
        key=lambda x: x["avg"], reverse=True,
    )
    return {
        "time": {"index": i, "iso": h["time"][i],
                 "label": datetime.fromisoformat(h["time"][i]).strftime("%a %b %-d, %-I %p"),
                 "temp": h["temperature_2m"][i], "cloud": h["cloud_cover"][i],
                 "wind_speed": h["wind_speed_10m"][i], "wind_dir": h["wind_direction_10m"][i]},
        "stands": items,
        "ranked": ranked,
    }


@router.post("/api/day/ranked")
async def day_ranked(body: DayRankIn, region_id: int = Depends(get_active_region_id), _=Depends(require_token)):
    """For a given day, score every stand across morning / midday / evening and
    return the full ranked list (by best period score) with each stand tagged for
    any period it wins."""
    region = get_region_dict(region_id)
    with Session(engine) as s:
        stands = [r.to_dict() for r in s.scalars(
            select(Stand).where(Stand.is_active == 1, Stand.region_id == region_id)).all()]
        first = s.scalars(select(Stand).where(Stand.region_id == region_id).order_by(Stand.name)).first()
        if not first:
            raise HTTPException(400, "no stands")
        lat, lon = first.lat, first.lon
    fc = await get_forecast(lat, lon, days=14, tz_name=region["property_timezone"])
    h = fc["hourly"]
    temp_swing_by_day = _temp_swing_by_day(h)
    sun = fc["daily"]
    day = body.day
    sr_h, ss_h = 6.5, 19.0
    for k in range(len(sun["sunrise"])):
        if sun["sunrise"][k][:10] == day:
            sr = datetime.fromisoformat(sun["sunrise"][k])
            ss = datetime.fromisoformat(sun["sunset"][k])
            sr_h = sr.hour + sr.minute / 60
            ss_h = ss.hour + ss.minute / 60

    # UTC offset for the property's location — used to convert stored UTC sighting
    # timestamps to local time before period matching in camera_boost.
    utc_offset = int(fc.get("utc_offset_seconds", 0))

    # period hour windows. midday spans whatever's actually left between morning
    # and evening (rather than a fixed 10-15) so no hour is ever stranded outside
    # all three windows — a fixed midday window left gaps on early-sunrise days
    # (morning could end before 10) and late-sunset days (evening could start
    # after 15). On a very short day this can make midday's range empty (lo > hi),
    # which is correct — score_period() then simply returns no result for it.
    morning_end = int(sr_h + 3)
    evening_start = int(ss_h - 3)
    periods = {
        "morning": (int(sr_h - 1), morning_end),
        "midday": (morning_end + 1, evening_start - 1),
        "evening": (evening_start, int(ss_h)),
    }
    # index hours of this day
    day_idxs = [i for i, t in enumerate(h["time"]) if t[:10] == day]
    if not day_idxs:
        raise HTTPException(400, "no forecast for that day")

    # proximity inputs
    with Session(engine) as s:
        zones = [z.to_dict() for z in s.scalars(
            select(Zone).where(Zone.is_active == 1, Zone.region_id == region_id)).all()]
        corridors_l = [c.to_dict() for c in s.scalars(
            select(Corridor).where(Corridor.is_active == 1, Corridor.region_id == region_id)).all()]
        sign_rows = [r.to_dict() for r in s.scalars(
            select(DeerSign).where(DeerSign.is_active == 1, DeerSign.region_id == region_id)).all()]
    settings = get_settings()
    # honor the per-type enable toggles from the rank list
    settings = dict(settings)
    if not body.use_corridor: settings["weight_corridor"] = 0.0
    if not body.use_food: settings["weight_food"] = 0.0
    if not body.use_bedding: settings["weight_bedding"] = 0.0
    max_cam_boost = float(settings.get("max_camera_boost_pct", 0.0) or 0.0)
    max_cam_penalty = float(settings.get("max_camera_penalty_pct", 0.0) or 0.0)
    cam_lookback = float(settings.get("camera_lookback_hours", 72.0) or 72.0)
    cam_health_max_age = float(settings.get("camera_health_max_age_hours", 48.0) or 48.0)
    cam_saturation = float(settings.get("camera_boost_saturation", 0.0)
                            or scoring.CAMERA_BOOST_SATURATION_DEFAULT)
    # Camera boost/penalty both depend on species classification actually
    # running (megadetector mode) — in fallback mode species is always
    # unknown, so neither can be evaluated correctly.
    camera_scoring_on = detection_mod.species_available()
    if not camera_scoring_on:
        max_cam_boost = max_cam_penalty = 0.0
    tp = _thermal_params(settings)

    # recent sightings + camera-presence per stand (lookback window handled
    # inside camera_boost)
    sightings_by_stand: dict[int, list] = {}
    with Session(engine) as s:
        for row in s.scalars(select(CameraSighting)).all():
            sightings_by_stand.setdefault(row.stand_id, []).append(
                {"timestamp": row.timestamp, "confidence_score": row.confidence_score,
                 "species": row.species})
    camera_status_by_stand = _camera_status_by_stand(region_id)

    def score_period(stand, lo, hi, bonus, period_name, sightings, has_camera, camera_ready,
                      camera_healthy, unhealthy_reason):
        best = None
        for i in day_idxs:
            hh = datetime.fromisoformat(h["time"][i]).hour
            if hh < lo or hh > hi:
                continue
            hour = {
                "wind_dir": h["wind_direction_10m"][i], "wind_speed": h["wind_speed_10m"][i],
                "gust": h["wind_gusts_10m"][i], "solar": h["shortwave_radiation"][i],
                "time_h": hh, "sunrise_h": sr_h, "sunset_h": ss_h,
                "temp_swing": temp_swing_by_day.get(day, 0.0),
            }
            det = scoring.score_with_breakdown(
                stand, hour, period=period_name, sightings=sightings,
                max_boost_pct=max_cam_boost, max_penalty_pct=max_cam_penalty,
                lookback_hours=cam_lookback, has_camera=has_camera, camera_ready=camera_ready,
                camera_healthy=camera_healthy, unhealthy_reason=unhealthy_reason,
                proximity=bonus, utc_offset_seconds=utc_offset, thermal_params=tp,
                camera_boost_saturation=cam_saturation)
            sc = {
                "total": det["final_score"],
                "base_total": det["base_score"],
                "proximity_bonus": det["proximity_bonus"],
                "camera": det["camera"],
                "breakdown": det["breakdown"],
                "scent_score": det["scent_score"],
                "scent_to_deg": det["scent_to_deg"],
                "thermal_phase": det["thermal_phase"],
            }
            if best is None or sc["total"] > best["score"]["total"]:
                best = {"hour": hour, "score": sc}
        return best

    # score each stand per period
    rows = []
    period_best = {p: None for p in periods}  # (stand_id, total)
    for st in stands:
        bonus = proximity_bonus(st, zones, corridors_l, settings, sign=sign_rows)
        sightings = sightings_by_stand.get(st["id"], [])
        cam_info = camera_status_by_stand.get(st["id"])
        has_camera = cam_info is not None
        camera_ready = has_camera and _camera_ready(cam_info["created_at"], cam_lookback)
        if has_camera:
            health = _camera_health(cam_info["last_seen_at"], cam_info["photo_count"],
                                     cam_info["photo_limit"], cam_health_max_age)
        else:
            health = {"healthy": True, "reason": None}
        per = {}
        for p, (lo, hi) in periods.items():
            b = score_period(st, lo, hi, bonus, p, sightings, has_camera, camera_ready,
                              health["healthy"], health["reason"])
            per[p] = b
            if b and (period_best[p] is None or b["score"]["total"] > period_best[p][1]):
                period_best[p] = (st["id"], b["score"]["total"])
        # the stand's headline score = its best period
        best_overall = max(
            [(p, per[p]["score"]["total"]) for p in periods if per[p]],
            key=lambda x: x[1], default=(None, 0),
        )
        rows.append({"stand": st, "periods": per, "best_period": best_overall[0], "best_score": best_overall[1],
                     "proximity": {k: round(v, 3) for k, v in bonus.items()}})

    wins = {p: (period_best[p][0] if period_best[p] else None) for p in periods}
    for row in rows:
        row["wins"] = [p for p in periods if wins[p] == row["stand"]["id"]]

    rows.sort(key=lambda r: r["best_score"], reverse=True)
    day_label = datetime.fromisoformat(day + "T12:00").strftime("%a %b %-d")
    return {"day": day, "day_label": day_label, "winners": wins, "ranked": rows}
