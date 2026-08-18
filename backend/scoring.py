"""Wind + thermal + scent scoring engine (server-side)."""
from __future__ import annotations
import math

DIRS = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
        "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"]


def deg_to_compass(d: float) -> str:
    return DIRS[round(((d % 360) + 360) % 360 / 22.5) % 16]


def compass_to_deg(c: str) -> float:
    return DIRS.index(c) * 22.5


def angle_diff(a: float, b: float) -> float:
    d = abs(a - b) % 360
    return 360 - d if d > 180 else d


def blend_scent_dir(wind_from, ww, thermal_to, tw) -> float:
    wind_to = (wind_from + 180) % 360
    wx = math.sin(math.radians(wind_to)) * ww
    wy = math.cos(math.radians(wind_to)) * ww
    tx = math.sin(math.radians(thermal_to)) * tw
    ty = math.cos(math.radians(thermal_to)) * tw
    return (math.degrees(math.atan2(wx + tx, wy + ty)) + 360) % 360


def thermal_state(time_h, solar, sr_h, ss_h) -> dict:
    a, b = time_h - sr_h, ss_h - time_h
    if -1 <= a <= 2:
        return {"phase": "sinking", "uphill": False, "weight": 0.85}
    if -1 <= b <= 3:
        return {"phase": "sinking", "uphill": False, "weight": 0.9}
    if time_h < sr_h - 1 or time_h > ss_h + 1:
        return {"phase": "sinking", "uphill": False, "weight": 0.7}
    sf = min(1.0, solar / 400)
    if sf > 0.25:
        return {"phase": "rising", "uphill": True, "weight": 0.4 + 0.4 * sf}
    return {"phase": "neutral", "uphill": False, "weight": 0.15}


def stand_hour_vectors(stand: dict, hour: dict) -> dict:
    """Return separate wind and thermal directions (blowing-TO, degrees) plus the
    blended scent direction and score — for map indicators that show wind and
    thermals as distinct arrows."""
    t = stand.get("terrain")
    downhill = t["downhill_deg"] if t else (stand.get("downhill_deg") or 0)
    drainage = t["drainage_deg"] if t else downhill
    therm = thermal_state(hour["time_h"], hour["solar"], hour["sunrise_h"], hour["sunset_h"])
    thermal_to = (downhill + 180) % 360 if therm["uphill"] else drainage
    wind_to = (hour["wind_dir"] + 180) % 360

    sc = score_stand_hour(stand, hour)
    return {
        "wind_to_deg": round(wind_to),
        "wind_from_deg": round(hour["wind_dir"]),
        "wind_speed": round(hour["wind_speed"], 1),
        "gust": round(hour["gust"], 1),
        "thermal_to_deg": round(thermal_to),
        "thermal_phase": therm["phase"],
        "thermal_uphill": therm["uphill"],
        "scent_to_deg": sc["scent_to_deg"],
        "scent_score": sc["scent_score"],
        "total": sc["total"],
    }


def score_stand_hour(stand: dict, hour: dict) -> dict:
    t = stand.get("terrain")
    downhill = t["downhill_deg"] if t else (stand.get("downhill_deg") or 0)
    drainage = t["drainage_deg"] if t else downhill
    therm = thermal_state(hour["time_h"], hour["solar"], hour["sunrise_h"], hour["sunset_h"])
    thermal_to = (downhill + 180) % 360 if therm["uphill"] else drainage

    ww = max(0.2, min(1.0, hour["wind_speed"] / 12))
    tw = therm["weight"] * (1.3 if hour["wind_speed"] < 6 else 0.8)
    if t and not therm["uphill"]:
        tw *= 0.8 + 0.5 * t["channel_strength"]

    scent_to = blend_scent_dir(hour["wind_dir"], ww, thermal_to, tw)

    scent_score = 1.0
    if stand.get("deer_approach_deg") is not None:
        scent_score = angle_diff(scent_to, stand["deer_approach_deg"]) / 180

    gust_spread = max(0, hour["gust"] - hour["wind_speed"])
    steadiness = 1 - min(0.5, gust_spread / 20)
    if hour["wind_speed"] < 2:
        steadiness -= 0.35
    if hour["wind_speed"] > 18:
        steadiness -= 0.3
    steadiness = max(0.0, steadiness)

    total = scent_score * 0.6 + steadiness * 0.25 + (0.15 if therm["phase"] != "neutral" else 0.05)
    return {
        "total": round(total, 3),
        "scent_score": round(scent_score, 2),
        "steadiness": round(steadiness, 2),
        "scent_to_deg": round(scent_to),
        "thermal_phase": therm["phase"],
        "drainage_deg": round(drainage),
    }
