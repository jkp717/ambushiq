"""Route handlers for forecast, wind/thermal ranking, and the map/day views.

Every ranking endpoint scores stands through one ScoringContext (forecast.service), so
the same stand at the same hour gets the same score on the map, in sit rankings and in
the day view."""
from __future__ import annotations

from datetime import date as _date
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.cameras import detection as detection_mod
from app.core.database import engine
from app.dependencies import get_active_region_id, require_token
from app.forecast import scoring
from app.forecast.providers import weather_provider_meta
from app.forecast.schemas import DayRankIn, HourRankIn, ManualRankIn, SitRankIn
from app.forecast.service import (
    _temp_swing_rolling,
    build_scoring_context,
    build_sits,
    format_day_label,
    format_hour_label,
    get_forecast,
)
from app.regions.service import get_region_dict
from app.settings.service import get_settings
from app.stands.models import Stand

router = APIRouter(tags=["forecast"])


def _sun_hours(fc: dict, day: str) -> tuple[float, float]:
    """(sunrise_h, sunset_h) for a forecast day, defaulting to 6:30 / 19:00."""
    sun = fc["daily"]
    for k in range(len(sun["sunrise"])):
        if sun["sunrise"][k][:10] == day:
            sr = datetime.fromisoformat(sun["sunrise"][k])
            ss = datetime.fromisoformat(sun["sunset"][k])
            return sr.hour + sr.minute / 60, ss.hour + ss.minute / 60
    return 6.5, 19.0


def _hour_at(h: dict, i: int, sr_h: float, ss_h: float, temp_swing: list) -> dict:
    return {
        "wind_dir": h["wind_direction_10m"][i], "wind_speed": h["wind_speed_10m"][i],
        "gust": h["wind_gusts_10m"][i], "solar": h["shortwave_radiation"][i],
        "time_h": datetime.fromisoformat(h["time"][i]).hour,
        "sunrise_h": sr_h, "sunset_h": ss_h,
        "temp_swing": temp_swing[i], "date": h["time"][i][:10],
    }


def _score_view(det: dict) -> dict:
    """The per-hour score shape the ranking endpoints return."""
    return {
        "total": det["final_score"],
        "base_total": det["base_score"],
        "proximity_bonus": det["proximity_bonus"],
        "camera": det["camera"],
        "breakdown": det["breakdown"],
        "scent_score": det["scent_score"],
        "scent_to_deg": det["scent_to_deg"],
        "thermal_phase": det["thermal_phase"],
        "drainage_deg": det["drainage_deg"],
    }


def _first_stand_location(region_id: int) -> tuple[float, float]:
    with Session(engine) as s:
        first = s.scalars(select(Stand).where(Stand.region_id == region_id).order_by(Stand.name)).first()
        if not first:
            raise HTTPException(400, "add a stand first")
        return first.lat, first.lon


def _active_stands(region_id: int) -> list[dict]:
    with Session(engine) as s:
        return [r.to_dict() for r in s.scalars(
            select(Stand).where(Stand.is_active == 1, Stand.region_id == region_id)).all()]


@router.get("/api/weather-providers")
def weather_providers(_=Depends(require_token)):
    """Provider metadata for the settings weather-source dropdown."""
    return {"providers": weather_provider_meta()}


@router.get("/api/forecast")
async def forecast_endpoint(region_id: int = Depends(get_active_region_id), _=Depends(require_token)):
    region = get_region_dict(region_id)
    lat, lon = _first_stand_location(region_id)
    try:
        fc = await get_forecast(lat, lon, tz_name=region["property_timezone"])
    except Exception as e:
        raise HTTPException(502, f"forecast unreachable: {e}")
    return {"sits": build_sits(fc)}


@router.post("/api/rank/sit")
async def rank_sit(body: SitRankIn, region_id: int = Depends(get_active_region_id), _=Depends(require_token)):
    """Rank stands for one sit: each stand's score is the AVERAGE of its hourly scores
    over the sit window (day_ranked, by contrast, reports each period's best hour)."""
    region = get_region_dict(region_id)
    stands = _active_stands(region_id)
    lat, lon = _first_stand_location(region_id)
    fc = await get_forecast(lat, lon, tz_name=region["property_timezone"])
    h = fc["hourly"]
    temp_swing = _temp_swing_rolling(h)
    mid = (body.sunrise_h + body.sunset_h) / 2
    windows = scoring.period_windows(body.sunrise_h, body.sunset_h)
    ctx = build_scoring_context(region_id, region, get_settings(), int(fc.get("utc_offset_seconds", 0)),
                                camera_scoring_on=detection_mod.species_available())
    results = []
    for st in stands:
        agg, n, sample = 0.0, 0, None
        for i in body.sit_idxs:
            hour = _hour_at(h, i, body.sunrise_h, body.sunset_h, temp_swing)
            det = ctx.score(st, hour, scoring.period_for_hour(hour["time_h"], windows), windows)
            agg += det["final_score"]
            n += 1
            dist = abs(hour["time_h"] - mid)
            if sample is None or dist < sample["dist"]:
                sample = {"hour": hour, "score": _score_view(det), "dist": dist}
        results.append({"stand": st, "avg": round(agg / max(1, n), 3), "sample": sample})
    results.sort(key=lambda x: x["avg"], reverse=True)
    return {"ranked": results}


@router.post("/api/rank/manual")
def rank_manual(body: ManualRankIn, region_id: int = Depends(get_active_region_id), _=Depends(require_token)):
    region = get_region_dict(region_id)
    stands = _active_stands(region_id)
    wind_from = scoring.compass_to_deg(body.wind_dir)
    time_h = {"morning": 7, "midday": 13, "evening": 18}.get(body.period, 13)
    solar = 500 if body.period == "midday" else 50
    ctx = build_scoring_context(region_id, region, get_settings(), 0,
                                camera_scoring_on=detection_mod.species_available())
    results = []
    for st in stands:
        hour = {"wind_dir": wind_from, "wind_speed": body.wind_speed, "gust": body.gust,
                "solar": solar, "time_h": time_h, "sunrise_h": 6.5, "sunset_h": 19,
                "date": _date.today().isoformat()}
        det = ctx.score(st, hour, body.period, scoring.DEFAULT_PERIOD_WINDOWS)
        results.append({"stand": st, "avg": det["final_score"], "sample": {"hour": hour, "score": _score_view(det)}})
    results.sort(key=lambda x: x["avg"], reverse=True)
    return {"ranked": results}


@router.get("/api/hours")
async def list_hours(region_id: int = Depends(get_active_region_id), _=Depends(require_token)):
    """Forecast hours grouped by day, for the day picker + hourly slider."""
    region = get_region_dict(region_id)
    lat, lon = _first_stand_location(region_id)
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
                              "label": format_day_label(datetime.fromisoformat(day + "T12:00")),
                              **sun_by_day.get(day, {"sunrise_h": 6.5, "sunset_h": 19, "sunrise": "", "sunset": ""}),
                              "hours": []})
        dt = datetime.fromisoformat(tstr)
        days[day]["hours"].append({"index": idx, "hour": dt.hour,
                                   "label": format_hour_label(dt)})
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
    return {"days": day_list, "utc_offset_seconds": utc_offset,
            "stale": bool(fc.get("stale")), "fetched_at": fc.get("fetched_at")}


@router.post("/api/map/conditions")
async def map_conditions(body: HourRankIn, region_id: int = Depends(get_active_region_id),
                          _=Depends(require_token)):
    """Per-stand wind + thermal vectors at one forecast hour, plus a ranked list in sync
    with that same hour. Drives the map indicators and the list together; scores come
    from the same ScoringContext as /api/day/ranked, so the two views always agree."""
    region = get_region_dict(region_id)
    stands = _active_stands(region_id)
    lat, lon = _first_stand_location(region_id)
    fc = await get_forecast(lat, lon, days=14, tz_name=region["property_timezone"])
    h = fc["hourly"]
    temp_swing = _temp_swing_rolling(h)
    i = body.time_index
    if i < 0 or i >= len(h["time"]):
        raise HTTPException(400, "time_index out of range")
    day = h["time"][i][:10]
    sr_h, ss_h = _sun_hours(fc, day)
    hour = _hour_at(h, i, sr_h, ss_h, temp_swing)
    windows = scoring.period_windows(sr_h, ss_h)
    period = scoring.period_for_hour(hour["time_h"], windows)

    settings = get_settings()
    ctx = build_scoring_context(region_id, region, settings, int(fc.get("utc_offset_seconds", 0)),
                                camera_scoring_on=detection_mod.species_available())

    items = []
    for st in stands:
        det = ctx.score(st, hour, period, windows)
        vec = dict(scoring.stand_hour_vectors(st, hour, ctx.thermal_params))
        vec["total"] = det["final_score"]
        if ctx.camera_scoring_on and st["id"] in ctx.camera_state:
            vec["camera_boost"] = det["camera"]
        items.append({"stand": st, "vectors": vec})

    ranked = sorted(
        [{"stand": it["stand"], "avg": it["vectors"]["total"],
          "sample": {"hour": hour, "score": {
              "scent_to_deg": it["vectors"]["scent_to_deg"],
              "scent_score": it["vectors"]["scent_score"],
              "thermal_phase": it["vectors"]["thermal_phase"],
          }}} for it in items],
        key=lambda x: x["avg"], reverse=True,
    )
    return {
        "time": {"index": i, "iso": h["time"][i],
                 "label": (lambda d: f"{format_day_label(d)}, {format_hour_label(d, lower=False)}")(
                     datetime.fromisoformat(h["time"][i])),
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
    stands = _active_stands(region_id)
    lat, lon = _first_stand_location(region_id)
    fc = await get_forecast(lat, lon, days=14, tz_name=region["property_timezone"])
    h = fc["hourly"]
    temp_swing = _temp_swing_rolling(h)
    day = body.day
    sr_h, ss_h = _sun_hours(fc, day)

    # The period windows are shared with the map and with trail-camera period matching
    # (see scoring.period_windows); midday can be empty on a very short day, in which case
    # score_period() simply returns no result for it.
    periods = scoring.period_windows(sr_h, ss_h)
    day_idxs = [i for i, t in enumerate(h["time"]) if t[:10] == day]
    if not day_idxs:
        raise HTTPException(400, "no forecast for that day")

    ctx = build_scoring_context(
        region_id, region, get_settings(), int(fc.get("utc_offset_seconds", 0)),
        camera_scoring_on=detection_mod.species_available(),
        toggles={"corridor": body.use_corridor, "food": body.use_food, "bedding": body.use_bedding})

    def score_period(stand, lo, hi, period_name):
        best = None
        for i in day_idxs:
            hh = datetime.fromisoformat(h["time"][i]).hour
            if hh < lo or hh > hi:
                continue
            hour = _hour_at(h, i, sr_h, ss_h, temp_swing)
            sc = _score_view(ctx.score(stand, hour, period_name, periods))
            if best is None or sc["total"] > best["score"]["total"]:
                best = {"hour": hour, "score": sc}
        return best

    rows = []
    period_best = {p: None for p in periods}  # (stand_id, total)
    for st in stands:
        per = {}
        for p, (lo, hi) in periods.items():
            b = score_period(st, lo, hi, p)
            per[p] = b
            if b and (period_best[p] is None or b["score"]["total"] > period_best[p][1]):
                period_best[p] = (st["id"], b["score"]["total"])
        # the stand's headline score = its best period
        best_overall = max(
            [(p, per[p]["score"]["total"]) for p in periods if per[p]],
            key=lambda x: x[1], default=(None, 0),
        )
        rows.append({"stand": st, "periods": per, "best_period": best_overall[0], "best_score": best_overall[1],
                     "proximity": {k: round(v, 3) for k, v in ctx.proximity_for(st, day).items()}})

    wins = {p: (period_best[p][0] if period_best[p] else None) for p in periods}
    for row in rows:
        row["wins"] = [p for p in periods if wins[p] == row["stand"]["id"]]

    rows.sort(key=lambda r: r["best_score"], reverse=True)
    day_label = format_day_label(datetime.fromisoformat(day + "T12:00"))
    return {"day": day, "day_label": day_label, "winners": wins, "ranked": rows}
