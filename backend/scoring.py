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


# ─────────────────────────── v2.15: camera boost + breakdowns ───────────────────────────
# Hunt-period hour windows (local hour-of-day) used to match daylight sightings.
PERIOD_WINDOWS = {
    "morning": (5, 10),   # 5–10 AM
    "midday": (10, 15),   # 10 AM–3 PM
    "evening": (15, 20),  # 3–8 PM
}


def period_for_hour(hour_of_day: int) -> str | None:
    for p, (lo, hi) in PERIOD_WINDOWS.items():
        if lo <= hour_of_day < hi:
            return p
    return None


def camera_boost(period: str, sightings: list[dict], max_boost_pct: float) -> dict:
    """
    Positive-only boost. Given a stand's recent camera sightings (each a dict with
    'timestamp' ISO and 'confidence_score'), return a multiplier >= 1.0 and a
    breakdown. Only DAYLIGHT sightings within the last 72h whose hour-of-day falls
    in the CURRENT hunt period count. No penalty is ever applied.

    Boost scales with how many qualifying sightings and their confidence, capped at
    max_boost_pct (e.g. 15.0 -> up to +15% -> multiplier up to 1.15).
    """
    import datetime as _dt
    now = _dt.datetime.now(_dt.timezone.utc)
    qualifying = []
    for s in sightings or []:
        ts = s.get("timestamp")
        if not ts:
            continue
        try:
            t = _dt.datetime.fromisoformat(ts.replace("Z", "+00:00"))
            if t.tzinfo is None:
                t = t.replace(tzinfo=_dt.timezone.utc)
        except Exception:
            continue
        age_h = (now - t).total_seconds() / 3600
        if age_h < 0 or age_h > 72:
            continue
        if period_for_hour(t.hour) != period:
            continue
        qualifying.append(s)

    if not qualifying:
        return {"multiplier": 1.0, "boost_pct": 0.0, "count": 0, "text": "no recent daylight photos"}

    # accumulate: each qualifying sighting contributes, weighted by confidence,
    # with diminishing returns; normalize so ~3 solid sightings approaches the cap.
    accum = 0.0
    for s in qualifying:
        conf = max(0.1, min(1.0, float(s.get("confidence_score") or 0.0) or 0.5))
        accum += conf
    frac = min(1.0, accum / 3.0)
    boost_pct = round(max_boost_pct * frac, 1)
    mult = 1.0 + boost_pct / 100.0
    return {
        "multiplier": mult, "boost_pct": boost_pct, "count": len(qualifying),
        "text": f"+{boost_pct}% from {len(qualifying)} daylight photo(s) in the last 72h",
    }


def score_with_breakdown(stand: dict, hour: dict, period: str | None = None,
                         sightings: list[dict] | None = None, max_boost_pct: float = 0.0,
                         proximity: dict | None = None) -> dict:
    """
    Wrap score_stand_hour with a structured, human-readable breakdown and an optional
    positive-only camera boost. Returns final_score plus a breakdown list.
    """
    base = score_stand_hour(stand, hour)
    base_total = base["total"]

    breakdown = []
    # wind / scent alignment
    if stand.get("deer_approach_deg") is not None:
        if base["scent_score"] > 0.6:
            wtxt = "scent carries away from expected deer approach"
        elif base["scent_score"] > 0.35:
            wtxt = "scent crosses the deer approach"
        else:
            wtxt = "scent blows toward deer"
    else:
        wtxt = "no deer-approach set; scent direction only"
    breakdown.append({"factor": "Wind / scent", "value": base["scent_score"],
                      "text": f"{wtxt} (scent to {base['scent_to_deg']}°)"})
    breakdown.append({"factor": "Wind steadiness", "value": base["steadiness"],
                      "text": f"gust steadiness {base['steadiness']}"})
    breakdown.append({"factor": "Terrain / thermals", "value": 1.0 if base["thermal_phase"] != "neutral" else 0.3,
                      "text": f"thermals {base['thermal_phase']}, drainage {base['drainage_deg']}°"})

    total = base_total
    prox_total = 0.0
    if proximity:
        prox_total = float(proximity.get("total") or 0.0)
        if prox_total > 0:
            total += prox_total
            breakdown.append({"factor": "Infrastructure proximity", "value": round(prox_total, 3),
                              "text": f"+{round(prox_total*100)} from nearby corridor/food/bedding"})

    cam = {"multiplier": 1.0, "boost_pct": 0.0, "count": 0, "text": "camera boost off"}
    if period and max_boost_pct and sightings is not None:
        cam = camera_boost(period, sightings, max_boost_pct)
        total *= cam["multiplier"]
        breakdown.append({"factor": "Trail-camera boost", "value": cam["boost_pct"] / 100.0,
                          "text": cam["text"]})

    return {
        "final_score": round(total, 3),
        "base_score": round(base_total, 3),
        "proximity_bonus": round(prox_total, 3),
        "camera": cam,
        "breakdown": breakdown,
        "scent_to_deg": base["scent_to_deg"],
        "thermal_phase": base["thermal_phase"],
        "scent_score": base["scent_score"],
    }
