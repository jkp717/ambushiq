"""Pluggable weather-forecast providers for AmbushIQ.

Mirrors the app.cameras.providers pattern: each brand/service implements
WeatherProvider.fetch() and normalizes its response to one shared shape:

    {
      "hourly": {
        "time": [...],                  # local-naive ISO "YYYY-MM-DDTHH:MM" strings
        "wind_direction_10m": [...],    # degrees
        "wind_speed_10m": [...],        # mph
        "wind_gusts_10m": [...],        # mph
        "shortwave_radiation": [...],   # W/m^2, or None where unavailable
        "temperature_2m": [...],        # Celsius
        "cloud_cover": [...],           # percent, or None where unavailable
        "surface_pressure": [...],      # hPa station-level, or None where unavailable
        "pressure_msl": [...],          # hPa reduced to sea level, or None where unavailable
        "precipitation": [...],         # mm per hour, or None where unavailable
        "dew_point_2m": [...],          # Celsius, or None where unavailable
      },
      "daily": {"sunrise": [...], "sunset": [...]},  # local-naive ISO datetimes, one per day
      "utc_offset_seconds": int,
    }

This is the exact shape Open-Meteo already returned, since the rest of the
app (scoring.py, the forecast/day-ranking routers, deer_ratings) was built
against it — every other provider normalizes into it so nothing downstream
has to change.

Open-Meteo and NWS are real, tested implementations (no API key required,
verified against live responses). OpenWeatherMap, Visual Crossing,
Tomorrow.io and WeatherAPI.com are implemented against each vendor's
documented response format but — like the non-SpyPoint brands in
app/cameras/providers.py — have not been exercised against a live paid
account in this environment; treat them as best-effort and expect to
adjust field names/units once run against a real key.
"""
from __future__ import annotations

import math
from datetime import date, datetime, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo

import httpx

from app.forecast.scoring import compass_to_deg


class WeatherError(Exception):
    pass


class WeatherProvider:
    id: str = ""
    label: str = ""
    needs_key: bool = False
    has_solar: bool = False

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key

    async def fetch(self, lat: float, lon: float, days: int, property_tz_name: str) -> dict:
        raise NotImplementedError


# ---------- shared helpers ----------

def _empty_hourly() -> dict:
    return {
        "time": [], "wind_direction_10m": [], "wind_speed_10m": [], "wind_gusts_10m": [],
        "shortwave_radiation": [], "temperature_2m": [], "cloud_cover": [],
        "surface_pressure": [], "pressure_msl": [], "precipitation": [], "dew_point_2m": [],
    }


HPA_PER_INHG = 33.8639


def _sun_times_utc(lat: float, lon: float, d: date, tz_name: str) -> tuple[Optional[datetime], Optional[datetime]]:
    """Sunrise/sunset (UTC) for one *local* calendar date at (lat, lon).

    Standard "Sunrise/Sunset Algorithm" (Almanac for Computers, 1990) —
    accurate to within about a minute, self-contained (no extra dependency).
    Used to fill sun times for providers that don't report them (NWS,
    Tomorrow.io). Returns (None, None) for the rare polar day/night case.
    """
    zenith = 90.833  # official sunrise/sunset zenith: 90° + refraction + solar radius
    lat_r = math.radians(lat)

    def calc_ut(is_sunrise: bool) -> Optional[float]:
        """Fractional UTC hour-of-day for the event, per the classic algorithm.
        This value alone doesn't say which UTC calendar day it belongs to —
        see the day-search below, which is what actually anchors it to `d`."""
        day_of_year = d.timetuple().tm_yday
        lng_hour = lon / 15
        t = day_of_year + ((6 - lng_hour) / 24 if is_sunrise else (18 - lng_hour) / 24)
        m = 0.9856 * t - 3.289
        l = m + 1.916 * math.sin(math.radians(m)) + 0.020 * math.sin(math.radians(2 * m)) + 282.634
        l %= 360
        ra = math.degrees(math.atan(0.91764 * math.tan(math.radians(l))))
        ra %= 360
        l_quadrant = math.floor(l / 90) * 90
        ra_quadrant = math.floor(ra / 90) * 90
        ra += l_quadrant - ra_quadrant
        ra /= 15
        sin_dec = 0.39782 * math.sin(math.radians(l))
        cos_dec = math.cos(math.asin(sin_dec))
        cos_h = (math.cos(math.radians(zenith)) - sin_dec * math.sin(lat_r)) / (cos_dec * math.cos(lat_r))
        if cos_h > 1 or cos_h < -1:
            return None
        h = math.degrees(math.acos(cos_h))
        h = 360 - h if is_sunrise else h
        h /= 15
        tt = h + ra - 0.06571 * t - 6.622
        return (tt - lng_hour) % 24

    def anchor(ut: Optional[float]) -> Optional[datetime]:
        if ut is None:
            return None
        # The raw UT hour doesn't indicate which UTC calendar day it falls on
        # (e.g. a 7pm sunset in a UTC-5 zone is 12:xxam UTC the *next* day).
        # Try d, d+1, d-1 and keep whichever one actually lands back on the
        # local calendar date `d` we were asked for.
        for day_shift in (0, 1, -1):
            candidate = datetime(d.year, d.month, d.day, tzinfo=timezone.utc) + timedelta(days=day_shift, hours=ut)
            try:
                if candidate.astimezone(ZoneInfo(tz_name)).date() == d:
                    return candidate
            except Exception:
                return candidate
        return datetime(d.year, d.month, d.day, tzinfo=timezone.utc) + timedelta(hours=ut)

    return anchor(calc_ut(True)), anchor(calc_ut(False))


def _tz_offset_seconds(tz_name: str, at: Optional[datetime] = None) -> int:
    at = at or datetime.now(timezone.utc)
    try:
        return int(at.astimezone(ZoneInfo(tz_name)).utcoffset().total_seconds())
    except Exception:
        return 0


def _to_local_naive(dt_utc: datetime, tz_name: str) -> str:
    try:
        local = dt_utc.astimezone(ZoneInfo(tz_name))
    except Exception:
        local = dt_utc
    return local.strftime("%Y-%m-%dT%H:%M")


# ---------- Open-Meteo (default; real, no key) ----------

class OpenMeteoProvider(WeatherProvider):
    id = "open_meteo"
    label = "Open-Meteo"
    needs_key = False
    has_solar = True

    async def fetch(self, lat: float, lon: float, days: int, property_tz_name: str) -> dict:
        url = (
            f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}"
            "&hourly=wind_direction_10m,wind_speed_10m,wind_gusts_10m,shortwave_radiation,"
            "temperature_2m,cloud_cover,surface_pressure,pressure_msl,precipitation,dew_point_2m"
            f"&daily=sunrise,sunset&wind_speed_unit=mph&timezone=auto&forecast_days={days}"
        )
        async with httpx.AsyncClient() as client:
            r = await client.get(url, timeout=20)
            r.raise_for_status()
            return r.json()


# ---------- National Weather Service (real, US-only, no key) ----------

class NWSProvider(WeatherProvider):
    id = "nws"
    label = "National Weather Service"
    needs_key = False
    has_solar = False

    async def fetch(self, lat: float, lon: float, days: int, property_tz_name: str) -> dict:
        headers = {"User-Agent": "(AmbushIQ, https://github.com/jkp717/ambushiq)"}
        # api.weather.gov answers a /points request with more than 4 decimal places with a 301 to the
        # 4-decimal URL (httpx doesn't follow redirects by default), so send 4 decimals and follow any redirect.
        async with httpx.AsyncClient(headers=headers, timeout=20, follow_redirects=True) as client:
            pts = await client.get(f"https://api.weather.gov/points/{lat:.4f},{lon:.4f}")
            if pts.status_code == 404:
                raise WeatherError("NWS has no forecast for this location — likely outside US coverage")
            pts.raise_for_status()   # 5xx is retried by the caller; other errors surface with their status
            pts_j = pts.json()["properties"]
            tz_name = pts_j.get("timeZone") or property_tz_name
            grid_url = pts_j["forecastGridData"]

            grid = await client.get(grid_url)
            grid.raise_for_status()
            gp = grid.json()["properties"]

        want_hours = min(days, 7) * 24  # NWS grid data covers ~7 days
        now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
        target_hours = [now + timedelta(hours=i) for i in range(want_hours)]

        def series(key: str, convert=lambda v: v, per_hour: bool = False) -> list:
            raw = (gp.get(key) or {}).get("values", [])
            return _nws_expand_series(raw, target_hours, convert, per_hour)

        hourly = {
            "time": [_to_local_naive(h, tz_name) for h in target_hours],
            "temperature_2m": series("temperature"),
            "wind_speed_10m": series("windSpeed", lambda v: round(v * 0.621371, 1)),
            "wind_gusts_10m": series("windGust", lambda v: round(v * 0.621371, 1)),
            "wind_direction_10m": series("windDirection"),
            "cloud_cover": series("skyCover"),
            "surface_pressure": series("pressure", lambda v: round(v / 100, 1)),  # Pa -> hPa
            "pressure_msl": [None] * len(target_hours),  # not reported by NWS grid data
            # quantitativePrecipitation values are totals over their validTime interval
            # (typically PT6H); spread them evenly so the hourly sum isn't overcounted.
            "precipitation": series("quantitativePrecipitation", per_hour=True),
            "dew_point_2m": series("dewpoint"),
            "shortwave_radiation": [None] * len(target_hours),  # not reported by NWS
        }

        days_seen, daily = set(), {"sunrise": [], "sunset": []}
        for h in target_hours:
            local_date = h.astimezone(ZoneInfo(tz_name)).date() if _safe_zone(tz_name) else h.date()
            if local_date in days_seen:
                continue
            days_seen.add(local_date)
            sr, ss = _sun_times_utc(lat, lon, local_date, tz_name)
            if sr and ss:
                daily["sunrise"].append(_to_local_naive(sr, tz_name))
                daily["sunset"].append(_to_local_naive(ss, tz_name))

        return {"hourly": hourly, "daily": daily, "utc_offset_seconds": _tz_offset_seconds(tz_name)}


def _nws_expand_series(raw: list, target_hours: list, convert=lambda v: v, per_hour: bool = False) -> list:
    """Expand NWS grid-data `values` (each valid over an ISO-8601 interval) onto
    hourly target times. With per_hour=True the value is an interval total and is
    divided by the interval length in hours instead of being repeated."""
    spans = []
    for entry in raw:
        vt = entry.get("validTime", "")
        if "/" not in vt:
            continue
        start_s, dur_s = vt.split("/", 1)
        try:
            start = datetime.fromisoformat(start_s)
        except ValueError:
            continue
        hours = _parse_iso_duration_hours(dur_s)
        spans.append((start, start + timedelta(hours=hours), hours, entry.get("value")))
    out = []
    for h in target_hours:
        val = None
        for start, end, hours, v in spans:
            if start <= h < end:
                val = v / hours if (per_hour and v is not None) else v
                break
        out.append(convert(val) if val is not None else None)
    return out


def _safe_zone(tz_name: str) -> bool:
    try:
        ZoneInfo(tz_name)
        return True
    except Exception:
        return False


def _parse_iso_duration_hours(dur: str) -> float:
    """Parse the hours component of a simple ISO-8601 duration like 'PT2H' or
    'PT30M' (NWS grid data never spans days within one run, so this only
    needs to handle the hour/minute case)."""
    import re
    m = re.match(r"^PT(?:(\d+)H)?(?:(\d+)M)?$", dur)
    if not m:
        return 1.0
    hours = int(m.group(1) or 0)
    minutes = int(m.group(2) or 0)
    return max(0.25, hours + minutes / 60)


# ---------- OpenWeatherMap (One Call 3.0; needs key) ----------

class OpenWeatherMapProvider(WeatherProvider):
    id = "openweathermap"
    label = "OpenWeatherMap"
    needs_key = True
    has_solar = False

    async def fetch(self, lat: float, lon: float, days: int, property_tz_name: str) -> dict:
        if not self.api_key:
            raise WeatherError("OpenWeatherMap requires an API key")
        url = (
            "https://api.openweathermap.org/data/3.0/onecall"
            f"?lat={lat}&lon={lon}&appid={self.api_key}&units=metric&exclude=minutely,alerts"
        )
        async with httpx.AsyncClient() as client:
            r = await client.get(url, timeout=20)
            if r.status_code == 401:
                raise WeatherError("OpenWeatherMap rejected the API key")
            r.raise_for_status()
            j = r.json()

        tz_name = j.get("timezone") or property_tz_name
        utc_offset = int(j.get("timezone_offset") or _tz_offset_seconds(tz_name))

        hourly_raw = j.get("hourly", [])
        hourly = _empty_hourly()
        for h in hourly_raw:
            dt = datetime.fromtimestamp(h["dt"], tz=timezone.utc)
            hourly["time"].append(_to_local_naive(dt, tz_name))
            hourly["temperature_2m"].append(h.get("temp"))
            hourly["wind_speed_10m"].append(round((h.get("wind_speed") or 0) * 2.23694, 1))
            hourly["wind_gusts_10m"].append(round((h.get("wind_gust", h.get("wind_speed", 0)) or 0) * 2.23694, 1))
            hourly["wind_direction_10m"].append(h.get("wind_deg"))
            hourly["cloud_cover"].append(h.get("clouds"))
            hourly["surface_pressure"].append(None)
            hourly["pressure_msl"].append(h.get("pressure"))  # One Call "pressure" is sea-level hPa
            hourly["precipitation"].append((h.get("rain") or {}).get("1h", 0.0))
            hourly["dew_point_2m"].append(h.get("dew_point"))
            hourly["shortwave_radiation"].append(None)  # not part of One Call 3.0

        daily = {"sunrise": [], "sunset": []}
        for d in (j.get("daily", []) or [])[:days]:
            if d.get("sunrise") and d.get("sunset"):
                daily["sunrise"].append(_to_local_naive(datetime.fromtimestamp(d["sunrise"], tz=timezone.utc), tz_name))
                daily["sunset"].append(_to_local_naive(datetime.fromtimestamp(d["sunset"], tz=timezone.utc), tz_name))

        return {"hourly": hourly, "daily": daily, "utc_offset_seconds": utc_offset}


# ---------- WeatherAPI.com (needs key; free tier capped at 3 forecast days) ----------

class WeatherAPIProvider(WeatherProvider):
    id = "weatherapi"
    label = "WeatherAPI.com"
    needs_key = True
    has_solar = False

    async def fetch(self, lat: float, lon: float, days: int, property_tz_name: str) -> dict:
        if not self.api_key:
            raise WeatherError("WeatherAPI.com requires an API key")
        req_days = max(1, min(days, 14))
        url = (
            f"https://api.weatherapi.com/v1/forecast.json?key={self.api_key}"
            f"&q={lat},{lon}&days={req_days}&aqi=no&alerts=no"
        )
        async with httpx.AsyncClient() as client:
            r = await client.get(url, timeout=20)
            if r.status_code == 401 or r.status_code == 403:
                raise WeatherError("WeatherAPI.com rejected the API key")
            r.raise_for_status()
            j = r.json()

        tz_name = j.get("location", {}).get("tz_id") or property_tz_name
        hourly = _empty_hourly()
        daily = {"sunrise": [], "sunset": []}
        for d in j.get("forecast", {}).get("forecastday", []):
            astro = d.get("astro", {})
            if astro.get("sunrise") and astro.get("sunset"):
                day_str = d["date"]
                daily["sunrise"].append(f"{day_str}T{_parse_12h(astro['sunrise'])}")
                daily["sunset"].append(f"{day_str}T{_parse_12h(astro['sunset'])}")
            for h in d.get("hour", []):
                hourly["time"].append(h["time"].replace(" ", "T"))
                hourly["temperature_2m"].append(h.get("temp_c"))
                hourly["wind_speed_10m"].append(h.get("wind_mph"))
                hourly["wind_gusts_10m"].append(h.get("gust_mph", h.get("wind_mph")))
                hourly["wind_direction_10m"].append(h.get("wind_degree"))
                hourly["cloud_cover"].append(h.get("cloud"))
                hourly["surface_pressure"].append(None)
                hourly["pressure_msl"].append(h.get("pressure_mb"))  # documented as sea-level mb
                hourly["precipitation"].append(h.get("precip_mm"))
                hourly["dew_point_2m"].append(h.get("dewpoint_c"))
                hourly["shortwave_radiation"].append(None)  # WeatherAPI has UV index only, not irradiance

        return {"hourly": hourly, "daily": daily, "utc_offset_seconds": _tz_offset_seconds(tz_name)}


def _parse_12h(s: str) -> str:
    """WeatherAPI astro times look like '6:12 AM' -> '06:12'."""
    try:
        return datetime.strptime(s, "%I:%M %p").strftime("%H:%M")
    except ValueError:
        return "06:00"


# ---------- Visual Crossing (needs key; has real solar radiation) ----------

class VisualCrossingProvider(WeatherProvider):
    id = "visual_crossing"
    label = "Visual Crossing"
    needs_key = True
    has_solar = True

    async def fetch(self, lat: float, lon: float, days: int, property_tz_name: str) -> dict:
        if not self.api_key:
            raise WeatherError("Visual Crossing requires an API key")
        url = (
            "https://weather.visualcrossing.com/VisualCrossingWebServices/rest/services/timeline/"
            f"{lat},{lon}?unitGroup=us&include=hours&key={self.api_key}&contentType=json"
        )
        async with httpx.AsyncClient() as client:
            r = await client.get(url, timeout=20)
            if r.status_code == 401:
                raise WeatherError("Visual Crossing rejected the API key")
            r.raise_for_status()
            j = r.json()

        tz_name = j.get("timezone") or property_tz_name
        hourly = _empty_hourly()
        daily = {"sunrise": [], "sunset": []}
        for d in (j.get("days", []) or [])[:max(1, min(days, 15))]:
            if d.get("sunrise") and d.get("sunset"):
                daily["sunrise"].append(f"{d['datetime']}T{d['sunrise'][:5]}")
                daily["sunset"].append(f"{d['datetime']}T{d['sunset'][:5]}")
            for h in d.get("hours", []):
                hourly["time"].append(f"{d['datetime']}T{h['datetime'][:5]}")
                hourly["temperature_2m"].append(_f_to_c(h.get("temp")))
                hourly["wind_speed_10m"].append(h.get("windspeed"))
                hourly["wind_gusts_10m"].append(h.get("windgust", h.get("windspeed")))
                hourly["wind_direction_10m"].append(h.get("winddir"))
                hourly["cloud_cover"].append(h.get("cloudcover"))
                hourly["surface_pressure"].append(None)
                hourly["pressure_msl"].append(h.get("pressure"))  # documented as sea-level mb
                hourly["precipitation"].append(_in_to_mm(h.get("precip")))
                hourly["dew_point_2m"].append(_f_to_c(h.get("dew")))  # unitGroup=us -> dew is °F
                hourly["shortwave_radiation"].append(h.get("solarradiation"))

        return {"hourly": hourly, "daily": daily, "utc_offset_seconds": int(float(j.get("tzoffset", 0)) * 3600)}


def _f_to_c(f: Optional[float]) -> Optional[float]:
    return round((f - 32) * 5 / 9, 1) if f is not None else None


def _in_to_mm(inches: Optional[float]) -> Optional[float]:
    return round(inches * 25.4, 1) if inches is not None else None


# ---------- Tomorrow.io (needs key) ----------

class TomorrowIoProvider(WeatherProvider):
    id = "tomorrow_io"
    label = "Tomorrow.io"
    needs_key = True
    has_solar = False

    async def fetch(self, lat: float, lon: float, days: int, property_tz_name: str) -> dict:
        if not self.api_key:
            raise WeatherError("Tomorrow.io requires an API key")
        url = (
            f"https://api.tomorrow.io/v4/weather/forecast?location={lat},{lon}"
            f"&apikey={self.api_key}&units=imperial&timesteps=1h"
        )
        async with httpx.AsyncClient() as client:
            r = await client.get(url, timeout=20)
            if r.status_code == 401:
                raise WeatherError("Tomorrow.io rejected the API key")
            r.raise_for_status()
            j = r.json()

        # Tomorrow.io doesn't report the location's timezone, so we fall back
        # to the property's configured timezone (same convention used for
        # normalizing trail-camera timestamps — see app.cameras.service).
        tz_name = property_tz_name
        hourly = _empty_hourly()
        for h in j.get("timelines", {}).get("hourly", [])[:days * 24]:
            dt = datetime.fromisoformat(h["time"].replace("Z", "+00:00"))
            v = h.get("values", {})
            hourly["time"].append(_to_local_naive(dt, tz_name))
            hourly["temperature_2m"].append(_f_to_c(v.get("temperature")))
            hourly["wind_speed_10m"].append(v.get("windSpeed"))
            hourly["wind_gusts_10m"].append(v.get("windGust", v.get("windSpeed")))
            hourly["wind_direction_10m"].append(v.get("windDirection"))
            hourly["cloud_cover"].append(v.get("cloudCover"))
            # units=imperial -> pressure fields are inHg; the shared shape is hPa
            surf, msl = v.get("pressureSurfaceLevel"), v.get("pressureSeaLevel")
            hourly["surface_pressure"].append(round(surf * HPA_PER_INHG, 1) if surf is not None else None)
            hourly["pressure_msl"].append(round(msl * HPA_PER_INHG, 1) if msl is not None else None)
            precip_rate = v.get("precipitationIntensity")
            hourly["precipitation"].append(round(precip_rate * 25.4, 1) if precip_rate is not None else None)
            hourly["dew_point_2m"].append(_f_to_c(v.get("dewPoint")))  # units=imperial -> dewPoint is °F
            hourly["shortwave_radiation"].append(None)  # not in the standard forecast timeline

        days_seen, daily = set(), {"sunrise": [], "sunset": []}
        for t in hourly["time"]:
            day_key = t[:10]
            if day_key in days_seen:
                continue
            days_seen.add(day_key)
            y, m, dnum = (int(x) for x in day_key.split("-"))
            sr, ss = _sun_times_utc(lat, lon, date(y, m, dnum), tz_name)
            if sr and ss:
                daily["sunrise"].append(_to_local_naive(sr, tz_name))
                daily["sunset"].append(_to_local_naive(ss, tz_name))

        return {"hourly": hourly, "daily": daily, "utc_offset_seconds": _tz_offset_seconds(tz_name)}


# ---------- registry ----------

_PROVIDER_CLASSES: dict[str, type[WeatherProvider]] = {
    p.id: p for p in (
        OpenMeteoProvider, NWSProvider, OpenWeatherMapProvider,
        WeatherAPIProvider, VisualCrossingProvider, TomorrowIoProvider,
    )
}


def get_weather_provider(provider_id: str, api_key: Optional[str] = None) -> WeatherProvider:
    cls = _PROVIDER_CLASSES.get(provider_id) or OpenMeteoProvider
    return cls(api_key=api_key)


def weather_provider_meta() -> list[dict]:
    return [
        {"id": p.id, "label": p.label, "needs_key": p.needs_key, "has_solar": p.has_solar}
        for p in _PROVIDER_CLASSES.values()
    ]
