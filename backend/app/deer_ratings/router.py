"""Route handlers for /api/deer-ratings."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import engine
from app.deer_ratings import rating as deer_rating
from app.dependencies import get_active_region_id, require_token
from app.forecast.service import (
    format_day_label,
    get_forecast,
    get_historical_day_weather,
    get_historical_highs_f,
    pressure_msl_series,
    rolling_baselines_f,
)
from app.regions.service import get_region_dict
from app.settings.service import get_settings
from app.stands.models import Stand

router = APIRouter(tags=["deer_ratings"])


@router.get("/api/deer-ratings")
async def deer_ratings(region_id: int = Depends(get_active_region_id), _=Depends(require_token)):
    """1-5 deer movement rating per forecast day, optimized for daytime movement."""
    from datetime import date as _date
    # local_today derived from the forecast UTC offset below, after fc is fetched.
    region = get_region_dict(region_id)
    with Session(engine) as s:
        first = s.scalars(select(Stand).where(Stand.region_id == region_id).order_by(Stand.name)).first()
        if not first:
            raise HTTPException(400, "add a stand first")
        lat, lon = first.lat, first.lon
    fc = await get_forecast(lat, lon, days=14, tz_name=region["property_timezone"])
    h = fc["hourly"]
    sun = fc["daily"]
    times = h["time"]
    # Derive today in property-local time from the forecast's UTC offset so that
    # confidence flags and days_out are correct during the evening UTC↔local gap.
    _utc_offset = int(fc.get("utc_offset_seconds", 0))
    _local_today = (datetime.now(timezone.utc) + timedelta(seconds=_utc_offset)).date()
    _set = get_settings()
    rate_weights = {
        "pressure": _set.get("rate_w_pressure"), "wind": _set.get("rate_w_wind"),
        "rain": _set.get("rate_w_rain"), "temp": _set.get("rate_w_temp"),
    }
    # The pressure sweet spot is a sea-level range: use the provider's sea-level series,
    # or reduce station pressure with the forecast elevation; None → neutral factor.
    pressures = pressure_msl_series(h, fc.get("elevation"))

    # sunrise/sunset per day for daytime windows
    sun_by_day = {}
    for i in range(len(sun["sunrise"])):
        sr = datetime.fromisoformat(sun["sunrise"][i]); ss = datetime.fromisoformat(sun["sunset"][i])
        sun_by_day[sun["sunrise"][i][:10]] = (sr.hour + sr.minute / 60, ss.hour + ss.minute / 60)

    # group hourly indices by day
    by_day = {}
    for i, t in enumerate(times):
        by_day.setdefault(t[:10], []).append(i)

    day_keys = sorted(by_day.keys())

    # daytime indices (sunrise..sunset) and forecast high per day
    day_idxs_by: dict[str, list[int]] = {}
    forecast_highs: dict[str, float] = {}
    for dk in day_keys:
        sr_h, ss_h = sun_by_day.get(dk, (6.5, 19.0))
        idxs = by_day[dk]
        day_idxs_by[dk] = [i for i in idxs if sr_h <= datetime.fromisoformat(times[i]).hour <= ss_h] or idxs
        temps = [h["temperature_2m"][i] for i in day_idxs_by[dk] if h["temperature_2m"][i] is not None]
        if temps:
            forecast_highs[dk] = max(temps) * 9 / 5 + 32

    # Each day's temperature baseline is the mean of its own trailing 7 daily highs:
    # observed highs (Open-Meteo archive) for past dates, forecast highs for future ones.
    # Never the mean of the whole forecast window (self-referential) and never anchored
    # to today (which made a seasonal cooling trend look like a front by day +13).
    past_highs = await get_historical_highs_f(lat, lon, _local_today - timedelta(days=1), days=7)
    baselines = rolling_baselines_f(day_keys, forecast_highs, past_highs)

    out = []
    for di, dk in enumerate(day_keys):
        day_idxs = day_idxs_by[dk]
        baseline_f = baselines.get(dk)

        def davg(arr):
            vals = [arr[i] for i in day_idxs if arr[i] is not None]
            return sum(vals) / len(vals) if vals else None

        wind_mph = davg(h["wind_speed_10m"])
        rain_mm = sum((h["precipitation"][i] if h.get("precipitation") else 0) or 0 for i in day_idxs) if h.get("precipitation") else \
                  sum((h["rain"][i] if h.get("rain") else 0) or 0 for i in day_idxs)
        high_c = max((h["temperature_2m"][i] for i in day_idxs), default=None)
        high_f = (high_c * 9 / 5 + 32) if high_c is not None else None
        dew_c = davg(h.get("dew_point_2m") or [None] * len(times))
        dew_f = (dew_c * 9 / 5 + 32) if dew_c is not None else None

        # pressure: daytime mean (hPa→inHg) and trend across the daytime window
        p_vals = [pressures[i] for i in day_idxs if pressures[i] is not None]
        p_inhg = (sum(p_vals) / len(p_vals) * deer_rating.HPA_TO_INHG) if p_vals else None
        p_trend = None
        if len(p_vals) >= 2:
            # inHg change per 3h, normalized over the window
            span = max(1, len(p_vals) - 1)
            p_trend = (p_vals[-1] - p_vals[0]) * deer_rating.HPA_TO_INHG / span * 3

        wx = {
            "pressure_inhg": round(p_inhg, 2) if p_inhg else None,
            "pressure_trend_inhg": round(p_trend, 3) if p_trend is not None else None,
            "wind_mph": round(wind_mph, 1) if wind_mph is not None else None,
            "rain_mm": round(rain_mm, 1),
            "day_high_f": round(high_f) if high_f is not None else None,
            "baseline_f": round(baseline_f) if baseline_f is not None else None,
            "dew_point_f": round(dew_f) if dew_f is not None else None,
        }
        y, m, d = (int(x) for x in dk.split("-"))
        rating = deer_rating.rate_day(_date(y, m, d), wx, rate_weights,
                                      rut_peak_month=int(region["rut_peak_month"]),
                                      rut_peak_day=int(region["rut_peak_day"]))
        label = format_day_label(datetime.fromisoformat(dk + "T12:00"))
        rating["day"] = dk
        rating["label"] = label
        # weather forecast is reliable ~7 days; flag beyond that.
        # Use property-local today (derived from utc_offset_seconds) so the
        # confidence flag doesn't flip prematurely during the evening UTC↔local gap.
        days_out = (_date(y, m, d) - _local_today).days
        rating["confidence"] = "high" if days_out <= 7 else "low"
        rating["days_out"] = days_out
        out.append(rating)

    # "Yesterday" is never part of the forward-looking forecast window above, so
    # today's card (the only one with no same-array predecessor) needs actual
    # observed weather for the day before, to show a day-over-day delta.
    previous_day = None
    yesterday = _local_today - timedelta(days=1)
    hist_wx = await get_historical_day_weather(lat, lon, yesterday)
    # The archive lags a day or two; a mostly-empty "yesterday" would rate on neutral
    # defaults and produce a misleading delta, so require most inputs to be present.
    core_inputs = ("pressure_inhg", "wind_mph", "rain_mm", "day_high_f", "baseline_f")
    if hist_wx and sum(hist_wx.get(k) is not None for k in core_inputs) >= 3:
        prev_rating = deer_rating.rate_day(yesterday, hist_wx, rate_weights,
                                            rut_peak_month=int(region["rut_peak_month"]),
                                            rut_peak_day=int(region["rut_peak_day"]))
        prev_rating["day"] = yesterday.isoformat()
        previous_day = prev_rating

    return {"ratings": out, "utc_offset_seconds": _utc_offset, "previous_day": previous_day}
