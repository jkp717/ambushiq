"""Hunt conditions for each day's morning and evening sit, shown in the day views (day chips, the selected
day, the stand cards). They're facts about a particular day's forecast, so they aren't bell alerts.

  swirl         the wind swings more than 90 degrees two or more times within the sit (property-wide:
                the forecast is one point), or
  eddy_stands   a likely lee eddy at a stand in any hour of the sit (forecast.eddy)
  rain          the wettest sit hour, when it reaches the `hunt_rain_mm` setting
  wind          the windiest sit hour, when wind or gusts reach `hunt_wind_mph` / `hunt_gust_mph`

A sit with nothing flagged is left out, so most days carry an empty dict."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from app.forecast import eddy as eddy_mod
from app.forecast import scoring
from app.forecast.scoring import angle_diff, deg_to_compass

SITS = ("morning", "evening")      # midday isn't a sit
MIN_SWINGS = 2
MM_PER_IN = 25.4


def wind_swings(dirs: list[float]) -> tuple[int, list[float]]:
    """How many times the wind swings more than 90 degrees away from the direction it last settled on,
    walking through a sit's hours, and the directions it swung between."""
    if not dirs:
        return 0, []
    path = [dirs[0]]
    for d in dirs[1:]:
        if angle_diff(d, path[-1]) > 90:
            path.append(d)
    return len(path) - 1, path


def _hour_label(t: str) -> str:
    h = datetime.fromisoformat(t).hour
    return f"{h % 12 or 12} {'AM' if h < 12 else 'PM'}"


def _num(series: Optional[list], i: int) -> Optional[float]:
    v = series[i] if series is not None and i < len(series) else None
    return None if v is None else float(v)


def sit_conditions(hourly: dict, idxs: list[int], stands: list[dict], sites: dict, settings: dict) -> dict:
    """Conditions over one sit's forecast hours (`idxs` into `hourly`); {} when nothing is flagged.
    `sites`: stand id -> EddySite (or None) for the lee-eddy check."""
    if not idxs:
        return {}
    out: dict = {}
    dirs = [_num(hourly.get("wind_direction_10m"), i) for i in idxs]
    swings, path = wind_swings([d for d in dirs if d is not None])
    if swings >= MIN_SWINGS:
        out["swirl"] = {"swings": swings, "dirs": [deg_to_compass(d) for d in path]}

    eddies = []
    for st in stands:
        site = sites.get(st["id"])
        for i in idxs:
            wd, ws = _num(hourly.get("wind_direction_10m"), i), _num(hourly.get("wind_speed_10m"), i)
            if wd is None or ws is None:
                continue
            e = eddy_mod.detect_lee_eddy(site, wd, ws, _num(hourly.get("wind_gusts_10m"), i) or ws)
            if e and e["level"] == "likely":
                eddies.append({"id": st["id"], "name": st["name"]})
                break
    if eddies:
        out["eddy_stands"] = eddies

    rain = [(_num(hourly.get("precipitation"), i) or 0.0, i) for i in idxs]
    mm, ri = max(rain)
    if mm >= float(settings.get("hunt_rain_mm", 2.5)):
        out["rain"] = {"in_per_h": round(mm / MM_PER_IN, 2), "at": _hour_label(hourly["time"][ri])}

    winds = [(_num(hourly.get("wind_speed_10m"), i) or 0.0, _num(hourly.get("wind_gusts_10m"), i) or 0.0, i)
             for i in idxs]
    top_wind = max(w for w, _g, _i in winds)
    top_gust, gi = max((g, i) for _w, g, i in winds)
    if top_wind >= float(settings.get("hunt_wind_mph", 18.0)) or top_gust >= float(settings.get("hunt_gust_mph", 30.0)):
        out["wind"] = {"mph": round(top_wind), "gust_mph": round(top_gust), "at": _hour_label(hourly["time"][gi])}
    return out


def eddy_sites(grid, stands: list[dict], min_inset_m: float) -> dict:
    """Where each stand reads lee-eddy terrain: its point in the property grid when well inside it, else its
    own stand-analysis grid (the same choice as ScoringContext.eddy_site + score_stand_hour)."""
    return {st["id"]: eddy_mod.site_for(grid, st, min_inset_m=min_inset_m) or eddy_mod.stand_site(st.get("terrain"))
            for st in stands}


def day_conditions(hourly: dict, day: str, sr_h: float, ss_h: float, stands: list[dict], sites: dict,
                   settings: dict) -> dict:
    """{"morning": {...}, "evening": {...}} for one day, leaving out sits with nothing flagged."""
    windows = scoring.period_windows(sr_h, ss_h)
    out = {}
    for sit in SITS:
        lo, hi = windows[sit]
        idxs = [i for i, t in enumerate(hourly["time"])
                if t[:10] == day and lo <= datetime.fromisoformat(t).hour <= hi]
        cond = sit_conditions(hourly, idxs, stands, sites, settings)
        if cond:
            out[sit] = cond
    return out
