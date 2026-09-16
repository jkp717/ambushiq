"""Business logic for forecast fetching, sit-building, and proximity/camera scoring inputs."""
from __future__ import annotations

import math
import time
from datetime import date, datetime, timedelta, timezone
from typing import Optional

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.cameras.models import Camera
from app.core.database import engine
from app.core.security import decrypt_settings_key
from app.forecast.providers import get_weather_provider
from app.settings.service import get_settings


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


def proximity_bonus(stand: dict, zones: list, corridors: list, settings: dict,
                    sign: list | None = None) -> dict:
    """Stacking, bonus-only proximity boost. Each feature contributes
    max(0, 1 - dist/falloff); summed per type and scaled by that type's weight."""
    slat, slon = stand["lat"], stand["lon"]

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
            total += max(0.0, 1 - d / falloff) if falloff > 0 else 0
        return total

    b_cor    = corridor_factor() * settings["weight_corridor"]
    b_food   = zone_factor("food",    settings["falloff_food"])    * settings["weight_food"]
    b_bed    = zone_factor("bedding", settings["falloff_bedding"]) * settings["weight_bedding"]
    b_scrape = sign_factor("scrape",  settings.get("falloff_scrape", 100)) * settings.get("weight_scrape", 0.12)
    b_rub    = sign_factor("rub",     settings.get("falloff_rub",    80))  * settings.get("weight_rub",    0.10)
    return {"corridor": b_cor, "food": b_food, "bedding": b_bed,
            "scrape": b_scrape, "rub": b_rub,
            "total": b_cor + b_food + b_bed + b_scrape + b_rub}


# ---------- forecast (server-side, short cache) ----------
_fc_cache: dict[str, tuple[float, dict]] = {}
FC_TTL = 1800  # 30 min


def _temp_swing_by_day(hourly: dict) -> dict[str, float]:
    """Return {YYYY-MM-DD: daily_high - daily_low (°C)} for every day in the
    forecast hourly block.  Used to scale thermal-drainage weights — a larger
    day/night temperature gradient produces denser cold air and stronger
    katabatic flow, which matters for scent prediction during morning and
    evening sits."""
    buckets: dict[str, list[float]] = {}
    temps = hourly.get("temperature_2m", [])
    times = hourly.get("time", [])
    for i, tstr in enumerate(times):
        if i < len(temps) and temps[i] is not None:
            buckets.setdefault(tstr[:10], []).append(float(temps[i]))
    return {d: max(v) - min(v) for d, v in buckets.items() if v}


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
    forecast = await primary.fetch(lat, lon, days, tz_name)

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
_hist_cache: dict[str, tuple[float, Optional[float]]] = {}
HIST_TTL = 12 * 3600  # actual past days never change; just avoids hammering the API


async def get_historical_baseline_f(lat: float, lon: float, end_date: date, days: int = 7) -> Optional[float]:
    """Actual (observed) daily-high average over the `days` ending `end_date`
    (inclusive), from Open-Meteo's free historical archive.

    This is what the deer-rating temp-shift factor compares each rated day
    against to detect a genuine cooling/warming departure from what's actually
    been normal recently — as opposed to the mean of the same forward-looking
    forecast window being rated, which is self-referential (every day in a
    forecast that trends warm or cold just compares against its own drifted
    average) and can't represent "recent" at all for the earliest rated days.

    Best-effort: returns None on any failure so callers fall back to their
    existing no-baseline behavior rather than breaking the rating endpoint.
    """
    key = f"{lat:.3f},{lon:.3f}:{end_date.isoformat()}:{days}"
    now = time.time()
    if key in _hist_cache and now - _hist_cache[key][0] < HIST_TTL:
        return _hist_cache[key][1]

    start = end_date - timedelta(days=days - 1)
    url = (
        "https://archive-api.open-meteo.com/v1/archive"
        f"?latitude={lat}&longitude={lon}&start_date={start.isoformat()}&end_date={end_date.isoformat()}"
        "&daily=temperature_2m_max&temperature_unit=fahrenheit&timezone=auto"
    )
    baseline: Optional[float] = None
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(url, timeout=15)
            r.raise_for_status()
            j = r.json()
        highs = [v for v in (j.get("daily", {}).get("temperature_2m_max") or []) if v is not None]
        if highs:
            baseline = sum(highs) / len(highs)
    except Exception:
        baseline = None

    _hist_cache[key] = (now, baseline)
    return baseline


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
        label = datetime.fromisoformat(day_str + "T12:00").strftime("%a %b %-d")
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

