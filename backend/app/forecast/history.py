"""Stored hourly weather history for each region, for charting camera activity against the weather.

Hours come from Open-Meteo's free historical archive and are stored for good: past weather doesn't
change. The archive runs about five days behind, so the most recent week comes from Open-Meteo's
forecast model (`past_days`) and is marked provisional; a later fill replaces it with archive values
(the daily weather_history job keeps the last two weeks current). Everything is best-effort: a failed
fetch leaves those hours missing and the chart simply has no weather there."""
from __future__ import annotations

import logging
import time
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone, tzinfo
from typing import Optional

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import engine
from app.forecast.models import WeatherHour
from app.regions.models import Region

log = logging.getLogger(__name__)

ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
RECENT_DAYS = 7               # days before today that come from the forecast model (provisional)
MAX_SPAN_DAYS = 366           # largest archive request
RECENT_TTL = 3600             # the provisional week is refreshed at most hourly
FAIL_TTL = 300                # a request that just failed isn't retried for this long
TIMEOUT_S = 20
HPA_TO_INHG = 0.02953         # matches app/deer_ratings/rating.py
HOURLY = "temperature_2m,pressure_msl,wind_speed_10m,precipitation"
UNITS = "&temperature_unit=fahrenheit&wind_speed_unit=mph&precipitation_unit=inch&timezone=GMT"

METRICS = {   # metric -> (column, unit, decimals)
    "temp": ("temp_f", "°F", 1),
    "pressure": ("pressure_inhg", "inHg", 2),
    "wind": ("wind_mph", "mph", 1),
    "rain": ("precip_in", "in", 2),
}

_recent_at: dict[int, float] = {}       # region -> when its provisional week was last fetched
_failed_at: dict[tuple, float] = {}     # request key -> when it last failed


def _get_json(url: str) -> Optional[dict]:
    with httpx.Client() as client:
        r = client.get(url, timeout=TIMEOUT_S)
        r.raise_for_status()
        return r.json()


def _parse(j: Optional[dict], provisional: bool, now: datetime) -> list[dict]:
    """Open-Meteo hourly arrays -> row dicts. Hours still in the future and hours with no values are skipped."""
    hh = (j or {}).get("hourly") or {}
    out = []
    cols = (hh.get("temperature_2m"), hh.get("pressure_msl"), hh.get("wind_speed_10m"), hh.get("precipitation"))
    for i, t in enumerate(hh.get("time") or []):
        temp, pres, wind, rain = (c[i] if c and i < len(c) else None for c in cols)
        if temp is None and pres is None and wind is None and rain is None:
            continue
        when = datetime.fromisoformat(t).replace(tzinfo=timezone.utc)
        if when > now:
            continue
        out.append({"hour_utc": when.strftime("%Y-%m-%dT%H:00Z"), "temp_f": temp,
                    "pressure_inhg": round(pres * HPA_TO_INHG, 3) if pres is not None else None,
                    "wind_mph": wind, "precip_in": rain, "provisional": 1 if provisional else 0})
    return out


def _upsert(region_id: int, rows: list[dict]) -> None:
    if not rows:
        return
    keys = [r["hour_utc"] for r in rows]
    with Session(engine) as s:
        have = {w.hour_utc: w for w in s.scalars(select(WeatherHour).where(
            WeatherHour.region_id == region_id, WeatherHour.hour_utc >= min(keys), WeatherHour.hour_utc <= max(keys)))}
        for r in rows:
            w = have.get(r["hour_utc"])
            if w is None:
                s.add(WeatherHour(region_id=region_id, **r))
            elif w.provisional or not r["provisional"]:      # archive values are never overwritten by provisional ones
                for k, v in r.items():
                    setattr(w, k, v)
        s.commit()


def _complete_days(region_id: int, start: date, end: date) -> set[date]:
    """UTC dates in [start, end] that already have all 24 archive (non-provisional) hours."""
    counts: dict[str, int] = defaultdict(int)
    with Session(engine) as s:
        for (h,) in s.execute(select(WeatherHour.hour_utc).where(
                WeatherHour.region_id == region_id, WeatherHour.provisional == 0,
                WeatherHour.hour_utc >= start.isoformat(), WeatherHour.hour_utc < (end + timedelta(days=1)).isoformat())):
            counts[h[:10]] += 1
    return {date.fromisoformat(d) for d, n in counts.items() if n >= 24}


def _runs(days: list[date]) -> list[tuple[date, date]]:
    """Sorted dates -> contiguous (first, last) runs, none longer than MAX_SPAN_DAYS."""
    runs: list[tuple[date, date]] = []
    for d in days:
        if runs and d == runs[-1][1] + timedelta(days=1) and (d - runs[-1][0]).days < MAX_SPAN_DAYS:
            runs[-1] = (runs[-1][0], d)
        else:
            runs.append((d, d))
    return runs


def _fetch(key: tuple, url: str, provisional: bool, now: datetime) -> list[dict]:
    if time.time() - _failed_at.get(key, 0) < FAIL_TTL:
        return []
    try:
        rows = _parse(_get_json(url), provisional, now)
    except Exception as e:
        log.warning("weather history: fetch failed (%s): %s: %s", key, type(e).__name__, e)
        rows = []
    if not rows:
        _failed_at[key] = time.time()
    return rows


def ensure_hours(region_id: int, start: date, end: date) -> None:
    """Make sure the stored history covers local dates `start`..`end` for this region, fetching what's missing:
    the archive for older days, the forecast model's past week for the newest ones. Never raises."""
    try:
        with Session(engine) as s:
            region = s.get(Region, region_id)
            if not region:
                return
            lat, lon = region.lat, region.lon
        now = datetime.now(timezone.utc)
        today = now.date()
        # local dates straddle two UTC dates, so pad by a day on each side
        lo, hi = start - timedelta(days=1), min(end + timedelta(days=1), today)
        if hi < lo:
            return
        recent_from = today - timedelta(days=RECENT_DAYS)
        done = _complete_days(region_id, lo, min(hi, recent_from - timedelta(days=1)))
        missing = [lo + timedelta(days=i) for i in range((hi - lo).days + 1)]
        archive_days = [d for d in missing if d < recent_from and d not in done]
        for a, b in _runs(archive_days):
            url = (f"{ARCHIVE_URL}?latitude={lat}&longitude={lon}&start_date={a.isoformat()}"
                   f"&end_date={b.isoformat()}&hourly={HOURLY}{UNITS}")
            _upsert(region_id, _fetch(("archive", region_id, a, b), url, False, now))
        if hi >= recent_from and time.time() - _recent_at.get(region_id, 0) >= RECENT_TTL:
            url = (f"{FORECAST_URL}?latitude={lat}&longitude={lon}&past_days={RECENT_DAYS}&forecast_days=1"
                   f"&hourly={HOURLY}{UNITS}")
            rows = _fetch(("recent", region_id), url, True, now)
            if rows:
                _upsert(region_id, rows)
                _recent_at[region_id] = time.time()
    except Exception:
        log.warning("weather history: couldn't fill region %s %s..%s", region_id, start, end, exc_info=True)


def weather_series(region_id: int, tz: tzinfo, by: str, metric: str, start: date, end: date) -> dict:
    """The stored weather for one metric over local dates `start`..`end`, shaped to the activity chart's bars.

    by="date": one point per local date — the average of its hours with the day's low and high. Rain instead
    gives the day's total (no whiskers: an hourly range can't share an axis with a daily total) and its
    wettest hour as `peak`.
    by="hour": one point per hour of the day — the average across the dates, with the lowest and highest.
    Points with no data have avg/low/high None."""
    col, unit, dec = METRICS[metric]
    lo = (start - timedelta(days=1)).isoformat()
    hi = (end + timedelta(days=2)).isoformat()
    with Session(engine) as s:
        rows = s.execute(select(WeatherHour.hour_utc, getattr(WeatherHour, col)).where(
            WeatherHour.region_id == region_id, WeatherHour.hour_utc >= lo, WeatherHour.hour_utc < hi)).all()
    groups: dict[object, list[float]] = defaultdict(list)
    for h, v in rows:
        if v is None:
            continue
        local = datetime.fromisoformat(h.replace("Z", "+00:00")).astimezone(tz)
        if start <= local.date() <= end:
            groups[local.date() if by == "date" else local.hour].append(float(v))

    def point(key, vals: list[float]) -> dict:
        p = {"key": key.isoformat() if isinstance(key, date) else key, "avg": None, "low": None, "high": None}
        if not vals:
            return p
        if metric == "rain" and by == "date":
            p.update(avg=round(sum(vals), dec), peak=round(max(vals), dec))
        else:
            p.update(avg=round(sum(vals) / len(vals), dec), low=round(min(vals), dec), high=round(max(vals), dec))
        return p

    keys = ([start + timedelta(days=i) for i in range((end - start).days + 1)] if by == "date" else range(24))
    return {"metric": metric, "unit": unit, "points": [point(k, groups.get(k, [])) for k in keys]}


def weather_history_job(progress) -> str:
    """Daily job: bring every region's last two weeks up to date (provisional days become archive days)."""
    with Session(engine) as s:
        regions = [(r.id, r.name) for r in s.scalars(select(Region)).all()]
    progress.set_total(len(regions))
    today = datetime.now(timezone.utc).date()
    for rid, name in regions:
        progress.begin(name)
        _recent_at.pop(rid, None)            # always refresh the provisional week here
        ensure_hours(rid, today - timedelta(days=14), today)
        progress.step()
    return f"{len(regions)} region{'' if len(regions) == 1 else 's'} updated"
