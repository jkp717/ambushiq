"""Business logic for forecast fetching, sit-building, and proximity/camera scoring inputs."""
from __future__ import annotations

import asyncio
import logging
import math
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Optional

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.cameras.models import Camera, CameraSighting
from app.core.database import engine
from app.core.security import decrypt_settings_key
from app.corridors.models import Corridor
from app.deer_ratings.rating import phase_proximity_multipliers, rut_intensity
from app.deer_sign.models import DeerSign
from app.forecast import scoring
from app.forecast.providers import WeatherError, _sun_times_utc, get_weather_provider
from app.settings.service import _thermal_params, get_settings
from app.zones.models import Zone


def _camera_status_by_stand(region_id: int) -> dict[int, dict]:
    """Per-stand snapshot of its earliest active, non-deleted camera: creation
    time (for the grace-period check) plus provider-reported health (for the
    unhealthy-camera check) — see _camera_ready() and _camera_health() below.
    A stand with multiple cameras just uses the earliest-created one; good
    enough for the common one-camera-per-stand case."""
    out: dict[int, dict] = {}
    with Session(engine) as s:
        rows = s.scalars(select(Camera).where(
            Camera.is_active == 1, Camera.is_deleted == 0, Camera.stand_id.isnot(None),
            Camera.region_id == region_id,
        )).all()
        for c in rows:
            if c.created_at and (c.stand_id not in out or c.created_at < out[c.stand_id]["created_at"]):
                out[c.stand_id] = {
                    "created_at": c.created_at, "last_seen_at": c.last_seen_at,
                    "photo_count": c.photo_count, "photo_limit": c.photo_limit,
                }
    return out


def _camera_ready(created_at: str | None, lookback_hours: float) -> bool:
    """True once a camera has been assigned long enough that an absence of
    deer photos over `lookback_hours` is meaningful, not just "no data yet"."""
    if not created_at:
        return False
    try:
        t = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        if t.tzinfo is None:
            t = t.replace(tzinfo=timezone.utc)
    except Exception:
        return False
    age_h = (datetime.now(timezone.utc) - t).total_seconds() / 3600
    return age_h >= lookback_hours


def _camera_health(last_seen_at: str | None, photo_count: int | None,
                    photo_limit: int | None, max_age_hours: float) -> dict:
    """Is this camera currently capable of capturing/transmitting anything?
    Used only to gate the camera PENALTY (never the boost — a real deer photo
    counts regardless of the camera's current health). Missing data defaults
    to "healthy" (no positive evidence of a problem), so brands/cameras that
    don't report these fields just behave as before."""
    if (photo_limit is not None and photo_limit != -1
            and photo_count is not None and photo_count >= photo_limit):
        return {"healthy": False, "reason": f"Photo quota reached ({photo_count}/{photo_limit})"}
    if last_seen_at:
        try:
            t = datetime.fromisoformat(last_seen_at.replace("Z", "+00:00"))
            if t.tzinfo is None:
                t = t.replace(tzinfo=timezone.utc)
            age_h = (datetime.now(timezone.utc) - t).total_seconds() / 3600
            if age_h > max_age_hours:
                return {"healthy": False, "reason": f"Camera hasn't checked in for {int(age_h)}h"}
        except Exception:
            pass
    return {"healthy": True, "reason": None}


def _haversine_m(lat1, lon1, lat2, lon2) -> float:
    from math import radians, sin, cos, asin, sqrt
    R = 6371000.0
    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)
    a = sin(dlat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2) ** 2
    return 2 * R * asin(sqrt(a))


def _point_to_segment_m(plat, plon, alat, alon, blat, blon) -> float:
    """Approx distance (m) from point P to segment AB using a local equirectangular
    projection — fine at property scale."""
    from math import radians, cos
    lat0 = radians((alat + blat) / 2)
    mlon = 111320.0 * cos(lat0)
    mlat = 110540.0
    ax, ay = (alon - plon) * mlon, (alat - plat) * mlat
    bx, by = (blon - plon) * mlon, (blat - plat) * mlat
    dx, dy = bx - ax, by - ay
    seg2 = dx * dx + dy * dy
    if seg2 == 0:
        return (ax * ax + ay * ay) ** 0.5
    t = max(0.0, min(1.0, -(ax * dx + ay * dy) / seg2))
    cx, cy = ax + t * dx, ay + t * dy
    return (cx * cx + cy * cy) ** 0.5


SIGN_HALF_LIFE_DAYS = {"scrape": 21.0, "rub": 60.0}   # scrapes go cold fast; rubs persist
SIGN_FRESHNESS_FLOOR = 0.25


def _soft_cap(x: float) -> float:
    """Diminishing returns for stacked features of one type: linear up to 1.0 (one
    ideal feature counts fully, exactly as before), then a smooth saturation toward
    1.5 — so twelve rubs are worth ~1.5 rubs, not twelve, and can't drown out
    wind/thermal conditions."""
    return x if x <= 1.0 else 1.0 + 0.5 * (1.0 - math.exp(-2.0 * (x - 1.0)))


def _sign_freshness(sg: dict, now: datetime) -> float:
    """Decay for a logged rub/scrape by how long ago it was recorded (created_at is when
    the user logged it — the best available proxy for when the deer made it). Missing or
    unparsable dates count as fresh."""
    raw = sg.get("created_at")
    if not raw:
        return 1.0
    try:
        t = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        if t.tzinfo is None:
            t = t.replace(tzinfo=timezone.utc)
    except ValueError:
        return 1.0
    age_days = max(0.0, (now - t).total_seconds() / 86400)
    half_life = SIGN_HALF_LIFE_DAYS.get(sg.get("kind"), 45.0)
    return max(SIGN_FRESHNESS_FLOOR, 0.5 ** (age_days / half_life))


def proximity_bonus(stand: dict, zones: list, corridors: list, settings: dict,
                    sign: list | None = None, multipliers: dict | None = None,
                    now: datetime | None = None) -> dict:
    """Bonus-only proximity boost. Each feature contributes max(0, 1 - dist/falloff);
    contributions are summed per type with diminishing returns (_soft_cap) and scaled
    by that type's weight, so the total is bounded (≤ 1.5× the sum of the weights).

    `multipliers` re-weights each type for the season (see
    deer_ratings.rating.phase_proximity_multipliers); rubs/scrapes decay with age."""
    slat, slon = stand["lat"], stand["lon"]
    mult = multipliers or {}
    now = now or datetime.now(timezone.utc)

    def zone_factor(kind, falloff):
        total = 0.0
        for z in zones:
            if z["kind"] != kind:
                continue
            d = max(0.0, _haversine_m(slat, slon, z["lat"], z["lon"]) - (z.get("radius_m") or 0))
            contrib = max(0.0, 1 - d / falloff) if falloff > 0 else 0
            # food zones: scale by quality using a steeper curve so top ratings stand out
            # (quality/10)^1.5 → rating 5 ≈ 35%, rating 8 ≈ 72%, rating 10 = 100%
            if kind == "food":
                quality = max(1, min(10, z.get("quality") or 5))
                contrib *= (quality / 10) ** 1.5
            total += contrib
        return total

    def corridor_factor():
        total = 0.0
        for c in corridors:
            # half the corridor's physical width -> a buffer of full-credit distance
            # around the centerline, mirroring zone_factor()'s radius_m buffer above
            half_width = max(0.0, float(c.get("width_m") or 0.0)) / 2.0
            # falloff: this stand's own visibility, else this corridor's override,
            # else the global setting — same fallback chain as before, with a new,
            # higher-priority first link so wide-sight stands "see" corridors farther
            falloff = stand.get("visibility_m") or c.get("falloff_m") or settings["falloff_corridor"]
            # usage 1-10 scales the contribution linearly (usage/10)
            usage_scale = max(1, min(10, c.get("usage") or 5)) / 10.0
            pts = c["points"]
            dmin = None
            for i in range(len(pts) - 1):
                d = _point_to_segment_m(slat, slon, pts[i][0], pts[i][1], pts[i + 1][0], pts[i + 1][1])
                dmin = d if dmin is None else min(dmin, d)
            if dmin is not None:
                edge_dist = max(0.0, dmin - half_width)  # 0 while inside the corridor's width
                contrib = max(0.0, 1 - edge_dist / falloff) if falloff > 0 else 0
                total += contrib * usage_scale
        return total

    def sign_factor(kind, falloff):
        total = 0.0
        for sg in (sign or []):
            if sg.get("kind") != kind or not sg.get("is_active", True):
                continue
            d = _haversine_m(slat, slon, sg["lat"], sg["lon"])
            total += (max(0.0, 1 - d / falloff) if falloff > 0 else 0) * _sign_freshness(sg, now)
        return total

    b_cor    = _soft_cap(corridor_factor()) * settings["weight_corridor"] * mult.get("corridor", 1.0)
    b_food   = _soft_cap(zone_factor("food",    settings["falloff_food"]))    * settings["weight_food"]    * mult.get("food", 1.0)
    b_bed    = _soft_cap(zone_factor("bedding", settings["falloff_bedding"])) * settings["weight_bedding"] * mult.get("bedding", 1.0)
    b_scrape = _soft_cap(sign_factor("scrape",  settings.get("falloff_scrape", 100))) * settings.get("weight_scrape", 0.12) * mult.get("scrape", 1.0)
    b_rub    = _soft_cap(sign_factor("rub",     settings.get("falloff_rub",    80)))  * settings.get("weight_rub",    0.10) * mult.get("rub", 1.0)
    return {"corridor": b_cor, "food": b_food, "bedding": b_bed,
            "scrape": b_scrape, "rub": b_rub,
            "total": b_cor + b_food + b_bed + b_scrape + b_rub}


# ---------- forecast (server-side, short cache) ----------
_fc_cache: dict[str, tuple[float, dict]] = {}
FC_TTL = 1800  # 30 min


def _temp_swing_rolling(hourly: dict, half_window: int = 12, min_points: int = 12) -> list[Optional[float]]:
    """Per-forecast-hour temperature swing (°C): max − min over the surrounding
    24 h window. Used to scale thermal-drainage weights — a larger day/night
    gradient means denser cold air and stronger katabatic flow.

    A rolling window (rather than the calendar day) lets an evening hour see the
    night that is actually about to follow it. Returns None where fewer than
    `min_points` readings exist so callers fall back to a neutral swing factor
    instead of treating "no data" as "no swing"."""
    temps = hourly.get("temperature_2m") or []
    n = len(temps)
    out: list[Optional[float]] = []
    for i in range(n):
        vals = [float(t) for t in temps[max(0, i - half_window):min(n, i + half_window + 1)] if t is not None]
        out.append(max(vals) - min(vals) if len(vals) >= min_points else None)
    return out


def format_day_label(dt: datetime) -> str:
    """'Sat Nov 8' — avoids strftime's '%-d', which raises ValueError on Windows."""
    return f"{dt.strftime('%a %b')} {dt.day}"


def format_hour_label(dt: datetime, lower: bool = True) -> str:
    """'7 am' / '7 AM' — avoids strftime's '%-I', which raises ValueError on Windows."""
    s = dt.strftime("%I %p").lstrip("0")
    return s.lower() if lower else s


def msl_from_station(p_hpa: Optional[float], elevation_m: Optional[float]) -> Optional[float]:
    """Reduce station-level pressure to sea level (standard-atmosphere barometric formula)."""
    if p_hpa is None or elevation_m is None:
        return None
    return p_hpa * (1 - 0.0065 * elevation_m / 288.15) ** -5.255


def pressure_msl_series(hourly: dict, elevation_m: Optional[float] = None) -> list[Optional[float]]:
    """Sea-level pressure (hPa) per forecast hour. The deer-rating pressure sweet spot
    (30.0-30.4 inHg) is a sea-level range, so station pressure must never be fed to it
    directly. Prefers the provider's own sea-level series; otherwise reduces station
    pressure using the forecast elevation; otherwise None (factor goes neutral)."""
    msl = hourly.get("pressure_msl") or []
    surf = hourly.get("surface_pressure") or []
    out: list[Optional[float]] = []
    for i in range(len(hourly.get("time", []))):
        v = msl[i] if i < len(msl) else None
        if v is None:
            v = msl_from_station(surf[i] if i < len(surf) else None, elevation_m)
        out.append(v)
    return out


def _fill_gaps(vals: list, default: Optional[float], interpolate: bool = False) -> None:
    """In-place gap fill: interior None runs are forward-filled (or linearly
    interpolated), leading gaps take the first valid value, and a series with no
    valid value at all becomes `default`."""
    valid = [i for i, v in enumerate(vals) if v is not None]
    if not valid:
        if default is not None:
            for i in range(len(vals)):
                vals[i] = default
        return
    for i in range(valid[0]):
        vals[i] = vals[valid[0]]
    for a, b in zip(valid, valid[1:] + [None]):
        end = len(vals) if b is None else b
        for i in range(a + 1, end):
            if interpolate and b is not None:
                vals[i] = vals[a] + (vals[b] - vals[a]) * (i - a) / (b - a)
            else:
                vals[i] = vals[a]


_HARD_REQUIRED_HOURLY_FIELDS = ("wind_direction_10m", "wind_speed_10m", "wind_gusts_10m",
                                "shortwave_radiation", "temperature_2m")


def _backfill_from_secondary(hourly: dict, secondary_hourly: dict) -> None:
    """Fill None entries in `hourly`'s hard-required fields (typically
    shortwave_radiation, when the primary provider doesn't report it) using
    the secondary provider's data for the same hour, matched by local hour."""
    by_hour = {t[:13]: i for i, t in enumerate(secondary_hourly.get("time", []))}
    for field in _HARD_REQUIRED_HOURLY_FIELDS:
        vals = hourly.get(field) or []
        sec_vals = secondary_hourly.get(field) or []
        for i, (t, v) in enumerate(zip(hourly.get("time", []), vals)):
            if v is not None:
                continue
            j = by_hour.get(t[:13])
            if j is not None and j < len(sec_vals) and sec_vals[j] is not None:
                vals[i] = sec_vals[j]


def _apply_safety_defaults(forecast: dict) -> None:
    """Last-resort fill for any hard-required hourly field still missing after
    the (optional) secondary-provider backfill, so the scoring engine — which
    does direct arithmetic on these values — never sees a None. Gust simply
    falls back to no extra spread; solar radiation falls back to a coarse
    daylight-triangle estimate (scaled down by cloud cover when known)."""
    hourly, daily = forecast["hourly"], forecast["daily"]
    times = hourly.get("time", [])
    # Wind speed/direction feed direct arithmetic in scoring (a single None → TypeError
    # → HTTP 500), so gaps are carried over from the neighboring hours; temperature
    # gaps are interpolated. Both are only fallbacks for occasional provider holes.
    _fill_gaps(hourly.setdefault("wind_speed_10m", []), 0.0)
    _fill_gaps(hourly.setdefault("wind_direction_10m", []), 0.0)
    _fill_gaps(hourly.setdefault("temperature_2m", []), None, interpolate=True)
    gust, wind = hourly.get("wind_gusts_10m") or [], hourly.get("wind_speed_10m") or []
    for i in range(len(gust)):
        if gust[i] is None:
            gust[i] = wind[i] if i < len(wind) else 0.0

    sun_by_day: dict[str, tuple[datetime, datetime]] = {}
    for i, day_str in enumerate([s[:10] for s in daily.get("sunrise", [])]):
        try:
            sun_by_day[day_str] = (datetime.fromisoformat(daily["sunrise"][i]),
                                    datetime.fromisoformat(daily["sunset"][i]))
        except (ValueError, IndexError):
            continue

    solar = hourly.get("shortwave_radiation") or []
    cloud = hourly.get("cloud_cover") or []
    for i, tstr in enumerate(times):
        if i >= len(solar) or solar[i] is not None:
            continue
        sun = sun_by_day.get(tstr[:10])
        try:
            hour_dt = datetime.fromisoformat(tstr)
        except ValueError:
            solar[i] = 0.0
            continue
        if not sun or not (sun[0] <= hour_dt <= sun[1]):
            solar[i] = 0.0
            continue
        span = (sun[1] - sun[0]).total_seconds() or 1
        frac = (hour_dt - sun[0]).total_seconds() / span
        cloud_i = cloud[i] if i < len(cloud) and cloud[i] is not None else 40
        solar[i] = round(700 * math.sin(math.pi * frac) * (1 - 0.75 * cloud_i / 100), 1)


class ForecastUnavailable(Exception):
    """The weather provider could not be reached (timeout, network error, 5xx, rejected key)
    and no cached forecast exists. main.py maps this to HTTP 502."""


FETCH_ATTEMPTS = 2
_RETRY_DELAY_S = 1.0


async def _fetch_with_retry(provider, lat: float, lon: float, days: int, tz_name: str) -> dict:
    """provider.fetch with one retry for transient failures (timeouts, connection errors,
    5xx). Anything still failing is raised as ForecastUnavailable."""
    last: Exception | None = None
    for attempt in range(FETCH_ATTEMPTS):
        try:
            return await provider.fetch(lat, lon, days, tz_name)
        except httpx.HTTPStatusError as e:
            last = e
            if e.response.status_code < 500:
                break                      # 4xx won't get better by retrying
        except httpx.TransportError as e:  # includes ReadTimeout / ConnectTimeout
            last = e
        except WeatherError as e:          # e.g. provider rejected the API key
            last = e
            break
        if attempt < FETCH_ATTEMPTS - 1:
            await asyncio.sleep(_RETRY_DELAY_S)
    raise ForecastUnavailable(f"weather provider unreachable: {str(last) or type(last).__name__}") from last


async def get_forecast(lat: float, lon: float, days: int = 3, tz_name: str | None = None) -> dict:
    settings = get_settings()
    tz_name = tz_name or "America/Chicago"
    primary_id = str(settings.get("weather_provider") or "open_meteo")
    secondary_id = str(settings.get("weather_secondary_provider") or "")

    # tz_name is part of the key: two regions at the same lat/lon with
    # different timezones must not share a cached forecast.
    key = f"{lat:.3f},{lon:.3f}:{days}:{primary_id}:{secondary_id}:{tz_name}"
    now = time.time()
    if key in _fc_cache and now - _fc_cache[key][0] < FC_TTL:
        return _fc_cache[key][1]

    primary = get_weather_provider(primary_id, decrypt_settings_key(settings.get("weather_provider_api_key")))
    try:
        forecast = await _fetch_with_retry(primary, lat, lon, days, tz_name)
    except ForecastUnavailable:
        # Upstream is down or slow: an expired cached forecast beats an error page.
        if key in _fc_cache:
            logging.getLogger(__name__).warning("weather provider unavailable; serving stale forecast for %s", key)
            return {**_fc_cache[key][1], "stale": True}
        raise

    if not primary.has_solar and secondary_id and secondary_id != primary_id:
        secondary = get_weather_provider(secondary_id, decrypt_settings_key(settings.get("weather_secondary_provider_api_key")))
        try:
            secondary_forecast = await secondary.fetch(lat, lon, days, tz_name)
            _backfill_from_secondary(forecast["hourly"], secondary_forecast["hourly"])
        except Exception:
            pass  # best-effort — the safety-net defaults below still cover any gaps

    _apply_safety_defaults(forecast)

    _fc_cache[key] = (now, forecast)
    return forecast


# ---------- historical baseline (deer-rating temp-shift factor) ----------
_hist_cache: dict[str, tuple[float, object]] = {}
HIST_TTL = 12 * 3600  # actual past days never change; just avoids hammering the API
HIST_FAIL_TTL = 300   # failures/empty results are retried soon (the archive lags a day or two)


def _hist_cache_get(key: str) -> tuple[bool, object]:
    hit = _hist_cache.get(key)
    if hit and time.time() - hit[0] < (HIST_TTL if hit[1] is not None else HIST_FAIL_TTL):
        return True, hit[1]
    return False, None


async def get_historical_highs_f(lat: float, lon: float, end_date: date, days: int = 7) -> Optional[dict[str, float]]:
    """Actual (observed) daily highs (°F) for the `days` ending `end_date` (inclusive),
    keyed by ISO date, from Open-Meteo's free historical archive. None on any failure."""
    key = f"highs:{lat:.3f},{lon:.3f}:{end_date.isoformat()}:{days}"
    hit, cached = _hist_cache_get(key)
    if hit:
        return cached  # type: ignore[return-value]

    start = end_date - timedelta(days=days - 1)
    url = (
        "https://archive-api.open-meteo.com/v1/archive"
        f"?latitude={lat}&longitude={lon}&start_date={start.isoformat()}&end_date={end_date.isoformat()}"
        "&daily=temperature_2m_max&temperature_unit=fahrenheit&timezone=auto"
    )
    result: Optional[dict[str, float]] = None
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(url, timeout=15)
            r.raise_for_status()
            j = r.json()
        daily = j.get("daily", {})
        pairs = zip(daily.get("time") or [], daily.get("temperature_2m_max") or [])
        result = {d: float(v) for d, v in pairs if v is not None} or None
    except Exception:
        result = None

    _hist_cache[key] = (time.time(), result)
    return result


async def get_historical_baseline_f(lat: float, lon: float, end_date: date, days: int = 7) -> Optional[float]:
    """Mean of the observed daily highs over the `days` ending `end_date` (inclusive).

    Best-effort: returns None on any failure so callers fall back to their
    no-baseline behavior rather than breaking the rating endpoint."""
    highs = await get_historical_highs_f(lat, lon, end_date, days)
    return sum(highs.values()) / len(highs) if highs else None


def rolling_baselines_f(day_keys: list[str], forecast_highs: dict[str, float],
                        past_highs: Optional[dict[str, float]], window: int = 7,
                        min_days: int = 3) -> dict[str, Optional[float]]:
    """Per-day temperature baseline: the mean of the `window` daily highs BEFORE each
    day, taken from observed highs for past dates and forecast highs for future ones.

    Every rated day is compared with its own trailing week, so a slow seasonal cooling
    across a 14-day forecast no longer reads as a front on the last days, and it is
    never self-referential (a day is not part of its own baseline). None when fewer
    than `min_days` prior highs are known (the temperature factor then goes neutral)."""
    known = {**(past_highs or {}), **forecast_highs}
    out: dict[str, Optional[float]] = {}
    for dk in day_keys:
        d = date.fromisoformat(dk)
        prior = [known[(d - timedelta(days=k)).isoformat()] for k in range(1, window + 1)
                 if (d - timedelta(days=k)).isoformat() in known]
        out[dk] = sum(prior) / len(prior) if len(prior) >= min_days else None
    return out


async def get_historical_day_weather(lat: float, lon: float, d: date) -> Optional[dict]:
    """Actual (observed) daytime-averaged weather for a single past date, from
    Open-Meteo's historical archive — used to rate "yesterday" for the
    day-over-day delta on today's rating card. The forward forecast window
    used for every other rated day never looks backward, so yesterday is
    never present in that array and has to be fetched separately here.

    Mirrors deer_ratings/router.py's own daytime-window averaging (sunrise..
    sunset, same field names) so the resulting factors are computed exactly
    the same way as every forecast-based day, making the two comparable.

    Best-effort: returns None on any failure (most commonly the archive not
    having ingested the last day or two yet) so the frontend just omits the
    delta rather than showing something misleading.
    """
    key = f"hday:{lat:.3f},{lon:.3f}:{d.isoformat()}"
    hit, cached = _hist_cache_get(key)
    if hit:
        return cached  # type: ignore[return-value]

    HPA_TO_INHG = 0.02953  # matches app/deer_ratings/rating.py's own constant
    url = (
        "https://archive-api.open-meteo.com/v1/archive"
        f"?latitude={lat}&longitude={lon}&start_date={d.isoformat()}&end_date={d.isoformat()}"
        "&hourly=temperature_2m,dew_point_2m,precipitation,wind_speed_10m,pressure_msl"
        "&daily=sunrise,sunset&temperature_unit=fahrenheit&wind_speed_unit=mph&timezone=auto"
    )
    result: Optional[dict] = None
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(url, timeout=15)
            r.raise_for_status()
            j = r.json()
        hh = j.get("hourly") or {}
        times = hh.get("time") or []
        daily = j.get("daily") or {}
        sr_list, ss_list = daily.get("sunrise") or [], daily.get("sunset") or []
        sr_h, ss_h = 6.5, 19.0
        if sr_list and ss_list:
            sr, ss = datetime.fromisoformat(sr_list[0]), datetime.fromisoformat(ss_list[0])
            sr_h, ss_h = sr.hour + sr.minute / 60, ss.hour + ss.minute / 60
        day_idxs = [i for i, t in enumerate(times) if sr_h <= datetime.fromisoformat(t).hour <= ss_h] or list(range(len(times)))

        def davg(field):
            arr = hh.get(field) or []
            vals = [arr[i] for i in day_idxs if i < len(arr) and arr[i] is not None]
            return sum(vals) / len(vals) if vals else None

        temp_arr = hh.get("temperature_2m") or []
        highs = [temp_arr[i] for i in day_idxs if i < len(temp_arr) and temp_arr[i] is not None]
        high_f = max(highs) if highs else None
        wind_mph = davg("wind_speed_10m")
        dew_f = davg("dew_point_2m")
        precip_arr = hh.get("precipitation") or []
        rain_vals = [precip_arr[i] for i in day_idxs if i < len(precip_arr) and precip_arr[i] is not None]
        rain_mm = sum(rain_vals) if rain_vals else None
        p_arr = hh.get("pressure_msl") or []
        p_vals = [p_arr[i] for i in day_idxs if i < len(p_arr) and p_arr[i] is not None]
        p_inhg = (sum(p_vals) / len(p_vals) * HPA_TO_INHG) if p_vals else None
        p_trend = None
        if len(p_vals) >= 2:
            span = max(1, len(p_vals) - 1)
            p_trend = (p_vals[-1] - p_vals[0]) * HPA_TO_INHG / span * 3

        baseline_f = await get_historical_baseline_f(lat, lon, d - timedelta(days=1))

        result = {
            "pressure_inhg": round(p_inhg, 2) if p_inhg else None,
            "pressure_trend_inhg": round(p_trend, 3) if p_trend is not None else None,
            "wind_mph": round(wind_mph, 1) if wind_mph is not None else None,
            "rain_mm": round(rain_mm, 1) if rain_mm is not None else None,
            "day_high_f": round(high_f) if high_f is not None else None,
            "baseline_f": round(baseline_f) if baseline_f is not None else None,
            "dew_point_f": round(dew_f) if dew_f is not None else None,
        }
    except Exception:
        result = None

    _hist_cache[key] = (time.time(), result)
    return result


def build_sits(forecast: dict) -> list[dict]:
    times = forecast["hourly"]["time"]
    sun = forecast["daily"]
    sits = []
    for day_i in range(len(sun["sunrise"])):
        sr = datetime.fromisoformat(sun["sunrise"][day_i])
        ss = datetime.fromisoformat(sun["sunset"][day_i])
        sr_h = sr.hour + sr.minute / 60
        ss_h = ss.hour + ss.minute / 60
        day_str = sun["sunrise"][day_i][:10]
        label = format_day_label(datetime.fromisoformat(day_str + "T12:00"))
        for tag, frm, to in (("morning", sr_h - 1, sr_h + 3), ("evening", ss_h - 3, ss_h + 0.5)):
            idxs = []
            lo, hi = math.floor(frm), math.ceil(to)
            for i, tstr in enumerate(times):
                if tstr[:10] != day_str:
                    continue
                h = datetime.fromisoformat(tstr).hour
                if lo <= h <= hi:
                    idxs.append(i)
            if idxs:
                sits.append({"label": f"{label} — {tag}", "idxs": idxs, "sunrise_h": sr_h, "sunset_h": ss_h})
    return sits


# ---------- unified stand scoring context ----------
SUN_MARGIN = timedelta(minutes=30)


@dataclass
class ScoringContext:
    """Everything scoring needs besides the stand and the hour, loaded once per request.

    Every ranking endpoint builds one of these and calls `score()`, which funnels into
    scoring.score_with_breakdown — so the same stand at the same hour gets the same
    number on the map, in sit rankings and in the day view."""
    settings: dict
    thermal_params: dict
    zones: list
    corridors: list
    sign: list
    sightings_by_stand: dict
    camera_state: dict
    camera_scoring_on: bool
    max_boost: float
    max_penalty: float
    lookback: float
    saturation: float
    utc_offset: int
    tz_name: str
    scent_gate_floor: float
    rut_peak: tuple
    rut_strength: float
    _prox: dict = field(default_factory=dict)
    _daylight: dict = field(default_factory=dict)
    _sun: dict = field(default_factory=dict)

    def season(self, day: Optional[str]) -> tuple[Optional[dict], Optional[str]]:
        """(proximity multipliers, rut phase) for a forecast date, or (None, None)
        when season weighting is off or no date is known."""
        if self.rut_strength <= 0 or not day:
            return None, None
        _, phase = rut_intensity(date.fromisoformat(day[:10]), *self.rut_peak)
        return phase_proximity_multipliers(phase, self.rut_strength), phase

    def proximity_for(self, stand: dict, day: Optional[str]) -> dict:
        key = (stand.get("id"), day[:10] if day else None)
        if key not in self._prox:
            mult, _ = self.season(day)
            self._prox[key] = proximity_bonus(stand, self.zones, self.corridors, self.settings,
                                              sign=self.sign, multipliers=mult)
        return self._prox[key]

    def _sun_utc(self, stand: dict, local_date: date):
        key = (stand["lat"], stand["lon"], local_date)
        if key not in self._sun:
            self._sun[key] = _sun_times_utc(stand["lat"], stand["lon"], local_date, self.tz_name)
        return self._sun[key]

    def daylight_sightings(self, stand: dict) -> list[dict]:
        """This stand's camera sightings taken between sunrise and sunset (± 30 min) of
        their own local date — a photo at 6:30 PM in December was almost certainly
        taken in the dark and says nothing about daylight deer movement."""
        sid = stand.get("id")
        if sid not in self._daylight:
            kept = []
            for sg in self.sightings_by_stand.get(sid, []):
                try:
                    t = datetime.fromisoformat(str(sg["timestamp"]).replace("Z", "+00:00"))
                except (KeyError, ValueError):
                    continue
                if t.tzinfo is None:
                    t = t.replace(tzinfo=timezone.utc)
                local_date = (t + timedelta(seconds=self.utc_offset)).date()
                sr, ss = self._sun_utc(stand, local_date)
                if sr and ss and not (sr - SUN_MARGIN <= t <= ss + SUN_MARGIN):
                    continue
                kept.append(sg)
            self._daylight[sid] = kept
        return self._daylight[sid]

    def score(self, stand: dict, hour: dict, period: Optional[str] = None,
              windows: Optional[dict] = None) -> dict:
        """Full score for one stand at one forecast hour. `hour["date"]` (ISO) selects the
        season weighting; `period` + `windows` select the camera evidence window."""
        day = hour.get("date")
        prox = dict(self.proximity_for(stand, day))
        _, phase = self.season(day)
        if phase:
            prox["season_phase"] = phase
        cam = self.camera_state.get(stand.get("id"))
        return scoring.score_with_breakdown(
            stand, hour, period=period if self.camera_scoring_on else None,
            sightings=self.daylight_sightings(stand) if cam else [],
            max_boost_pct=self.max_boost, max_penalty_pct=self.max_penalty,
            lookback_hours=self.lookback, has_camera=cam is not None,
            camera_ready=cam["ready"] if cam else True,
            camera_healthy=cam["healthy"] if cam else True,
            unhealthy_reason=cam["reason"] if cam else None,
            proximity=prox, utc_offset_seconds=self.utc_offset,
            thermal_params=self.thermal_params, camera_boost_saturation=self.saturation,
            scent_gate_floor=self.scent_gate_floor, windows=windows)


def build_scoring_context(region_id: int, region: dict, settings: dict, utc_offset: int, *,
                          camera_scoring_on: bool, toggles: Optional[dict] = None) -> ScoringContext:
    """Load zones/corridors/sign/camera evidence for a region once. `toggles`
    ({"corridor": bool, "food": bool, "bedding": bool}) zeroes those proximity weights;
    `camera_scoring_on` is whether species classification is actually running (in
    fallback mode species is unknown, so neither boost nor penalty can be evaluated)."""
    settings = dict(settings)
    for kind in ("corridor", "food", "bedding"):
        if toggles and not toggles.get(kind, True):
            settings[f"weight_{kind}"] = 0.0

    lookback = float(settings.get("camera_lookback_hours", 72.0) or 72.0)
    health_max_age = float(settings.get("camera_health_max_age_hours", 48.0) or 48.0)
    max_boost = float(settings.get("max_camera_boost_pct", 0.0) or 0.0)
    max_penalty = float(settings.get("max_camera_penalty_pct", 0.0) or 0.0)
    if not camera_scoring_on:
        max_boost = max_penalty = 0.0

    camera_status = _camera_status_by_stand(region_id)
    camera_state = {}
    for sid, info in camera_status.items():
        health = _camera_health(info["last_seen_at"], info["photo_count"], info["photo_limit"], health_max_age)
        camera_state[sid] = {"ready": _camera_ready(info["created_at"], lookback),
                             "healthy": health["healthy"], "reason": health["reason"]}

    sightings_by_stand: dict[int, list] = {}
    with Session(engine) as s:
        zones = [z.to_dict() for z in s.scalars(
            select(Zone).where(Zone.is_active == 1, Zone.region_id == region_id)).all()]
        corridors = [c.to_dict() for c in s.scalars(
            select(Corridor).where(Corridor.is_active == 1, Corridor.region_id == region_id)).all()]
        sign = [r.to_dict() for r in s.scalars(
            select(DeerSign).where(DeerSign.is_active == 1, DeerSign.region_id == region_id)).all()]
        if camera_scoring_on and camera_status:
            for row in s.scalars(select(CameraSighting).where(
                    CameraSighting.stand_id.in_(list(camera_status)))).all():
                sightings_by_stand.setdefault(row.stand_id, []).append(
                    {"timestamp": row.timestamp, "confidence_score": row.confidence_score,
                     "species": row.species})

    return ScoringContext(
        settings=settings, thermal_params=_thermal_params(settings),
        zones=zones, corridors=corridors, sign=sign,
        sightings_by_stand=sightings_by_stand, camera_state=camera_state,
        camera_scoring_on=camera_scoring_on, max_boost=max_boost, max_penalty=max_penalty,
        lookback=lookback,
        saturation=float(settings.get("camera_boost_saturation", 0.0) or scoring.CAMERA_BOOST_SATURATION_DEFAULT),
        utc_offset=utc_offset, tz_name=region.get("property_timezone") or "America/Chicago",
        scent_gate_floor=float(settings.get("scent_gate_floor", scoring.SCENT_GATE_FLOOR_DEFAULT)),
        rut_peak=(int(region["rut_peak_month"]), int(region["rut_peak_day"])),
        rut_strength=float(settings.get("rut_weight_strength", 1.0)),
    )

