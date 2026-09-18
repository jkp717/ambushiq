"""Wind + thermal + scent scoring engine (server-side)."""
from __future__ import annotations
import math
import datetime as _dt  # module-level import (was incorrectly inside camera_boost)

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


def thermal_state(time_h, solar, sr_h, ss_h, temp_swing=None) -> dict:
    """Return the thermal phase and weight for one forecast hour.

    temp_swing: daily high − daily low in °C from the forecast.  When provided,
    sinking-phase weights are scaled by a swing_factor: a larger day/night
    temperature gradient means denser cold air and stronger katabatic drainage.
    Baseline (swing_factor = 1.0) is calibrated to a 12 °C / 22 °F swing —
    typical clear-sky fall conditions.  Without temp data the original weights
    are used unchanged (swing_factor = 1.0).
    """
    # swing_factor: 0.5 (near-zero swing) → 1.0 (12 °C / 22 °F) → 1.4 (≥ 24 °C / 43 °F)
    if temp_swing is None:
        swing_factor = 1.0
    else:
        swing_factor = min(1.4, max(0.5, float(temp_swing) / 12.0))

    # solar_frac (0..1) is the raw anabatic "potential" from ground heating — it
    # keeps building through midday. Returned unconditionally so callers can feed
    # it into thermal_coherence() even for sinking/neutral hours.
    sf = min(1.0, max(0.0, solar / 400))

    a, b = time_h - sr_h, ss_h - time_h
    if -1 <= a <= 2:
        return {"phase": "sinking", "uphill": False,
                "weight": min(1.0, 0.85 * swing_factor), "swing_factor": swing_factor, "solar_frac": sf}
    # Pre-sunset drainage only takes over once the sun has actually stopped heating the
    # slope; on a bright late afternoon (sf > 0.25) upslope flow is still running, so
    # fall through to the "rising" branch instead of reporting a 180°-wrong direction.
    if -1 <= b <= 3 and sf <= 0.25:
        return {"phase": "sinking", "uphill": False,
                "weight": min(1.0, 0.90 * swing_factor), "swing_factor": swing_factor, "solar_frac": sf}
    if time_h < sr_h - 1 or time_h > ss_h + 1:
        return {"phase": "sinking", "uphill": False,
                "weight": min(1.0, 0.70 * swing_factor), "swing_factor": swing_factor, "solar_frac": sf}
    if sf > 0.25:
        return {"phase": "rising", "uphill": True,
                "weight": 0.4 + 0.4 * sf, "swing_factor": swing_factor, "solar_frac": sf}
    return {"phase": "neutral", "uphill": False, "weight": 0.15, "swing_factor": swing_factor, "solar_frac": sf}


# ─────────────────────── thermal coherence (wind/mixing gate) ───────────────────────
# thermal_state()'s "weight" is thermal *potential* — it legitimately builds through
# the day as the sun heats the ground. thermal_coherence() is separate: it answers
# "how much of that potential actually shows up as a clean directional signal in the
# blended scent vector, vs. getting overwhelmed by ambient wind or scrambled by midday
# convective mixing." A strong thermal can still lose the vector blend to a stronger,
# more turbulent wind — this is what makes calm dawns thermal-dominated and breezy
# middays wind-dominated even though the raw upslope flow is strongest near midday.
DEFAULT_THERMAL_PARAMS = {
    "wind_half_scale": 7.0,   # mph at which wind has cut thermal coherence roughly in half
    "wind_exponent": 1.8,     # how sharply coherence falls off past the half-scale point
    "midday_discount": 0.3,   # extra coherence knocked off "rising" phase even at calm wind
    "thermal_gain": 1.3,      # calm-air boost so a clean dawn/dusk thermal can out-vote light wind
    "coherence_floor": 0.05,  # coherence never drops below this, even in a gale
}

# Hard ceiling on the blended thermal weight so a tuned-up gain can never swamp the wind vector
TW_MAX = 1.5


def thermal_coherence(wind_speed: float, phase: str, solar_frac: float,
                      params: dict | None = None) -> float:
    p = {**DEFAULT_THERMAL_PARAMS, **(params or {})}
    half_scale = max(0.1, float(p["wind_half_scale"]))
    exponent = max(0.1, float(p["wind_exponent"]))
    wind_gate = 1.0 / (1.0 + (max(0.0, wind_speed) / half_scale) ** exponent)
    if phase == "rising":
        discount = max(0.0, min(1.0, float(p["midday_discount"])))
        wind_gate *= (1.0 - discount * solar_frac)
    return max(float(p["coherence_floor"]), wind_gate * float(p["thermal_gain"]))


def _terrain_vectors(stand: dict) -> tuple[float, float, float | None, bool]:
    """(downhill_deg, drainage_deg, channel_strength, known) for a stand.

    `known` is False when nothing reliable says which way air drains — terrain not
    analysed yet and no manual downhill bearing, or flat ground — so callers give
    the thermal vector zero weight instead of inventing a due-north drainage."""
    t = stand.get("terrain")
    if t:
        if t.get("flat"):
            return 0.0, 0.0, None, False
        return t["downhill_deg"], t["drainage_deg"], t.get("channel_strength"), True
    manual = stand.get("downhill_deg")
    if manual is not None:
        return manual, manual, None, True
    return 0.0, 0.0, None, False


def stand_hour_vectors(stand: dict, hour: dict, thermal_params: dict | None = None) -> dict:
    """Return separate wind and thermal directions (blowing-TO, degrees) plus the
    blended scent direction and score — for map indicators that show wind and
    thermals as distinct arrows."""
    wind_to = (hour["wind_dir"] + 180) % 360

    sc = score_stand_hour(stand, hour, thermal_params)
    # The thermal arrow is only meaningful when the drainage direction is known and the
    # phase actually has a coherent flow; in "neutral" hours the direction is noise.
    show_thermal = sc["thermal_known"] and sc["thermal_phase"] != "neutral"
    return {
        "wind_to_deg": round(wind_to),
        "wind_from_deg": round(hour["wind_dir"]),
        "wind_speed": round(hour["wind_speed"], 1),
        "gust": round(hour["gust"], 1),
        "thermal_to_deg": sc["thermal_to_deg"] if show_thermal else None,
        "thermal_strength": round(min(1.0, sc["tw"]), 2) if show_thermal else 0.0,
        "thermal_phase": sc["thermal_phase"],
        "thermal_uphill": sc["thermal_uphill"],
        "scent_to_deg": sc["scent_to_deg"],
        "scent_score": sc["scent_score"],
        "total": sc["total"],
    }


def score_stand_hour(stand: dict, hour: dict, thermal_params: dict | None = None) -> dict:
    downhill, drainage, channel, known = _terrain_vectors(stand)
    therm = thermal_state(hour["time_h"], hour["solar"], hour["sunrise_h"], hour["sunset_h"],
                          hour.get("temp_swing"))
    thermal_to = (downhill + 180) % 360 if therm["uphill"] else drainage

    ww = max(0.2, min(1.0, hour["wind_speed"] / 12))
    coherence = thermal_coherence(hour["wind_speed"], therm["phase"], therm["solar_frac"], thermal_params)
    if known:
        tw = therm["weight"] * coherence
        if channel is not None and not therm["uphill"]:
            tw *= 0.8 + 0.5 * channel
        tw = min(TW_MAX, tw)
    else:
        tw = 0.0  # no reliable drainage direction → the scent vector is just the wind

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

    # "conditions" score: wind steadiness + thermal predictability (0..1).
    # Scent direction is NOT included here — score_with_breakdown applies it afterwards as
    # a multiplicative (soft, configurable) gate over conditions + proximity + camera.
    # Thermal predictability only counts when the drainage direction is known, the phase
    # is coherent, and ambient wind hasn't washed it out (coherence scales the credit).
    predictable = known and therm["phase"] != "neutral"
    thermal_credit = 0.1 + 0.2 * min(1.0, coherence) if predictable else 0.1
    conditions = steadiness * 0.7 + thermal_credit

    # "total" = conditions × scent (hard gate, no proximity/camera) — a quick base score for
    # callers that don't need the full score_with_breakdown pipeline.
    total = conditions * scent_score

    return {
        "total": round(total, 3),
        "conditions": round(conditions, 3),  # pre-scent, consumed by score_with_breakdown
        "scent_score": round(scent_score, 2),
        "steadiness": round(steadiness, 2),
        "scent_to_deg": round(scent_to),
        "thermal_phase": therm["phase"],
        "thermal_known": known,
        "thermal_uphill": therm["uphill"],
        "thermal_to_deg": round(thermal_to) if known else None,
        "drainage_deg": round(drainage) if known else None,
        "tw": round(tw, 3),
        "ww": round(ww, 3),
        "swing_factor": round(therm["swing_factor"], 2),
    }


# ─────────────────────────── v2.15: camera boost + breakdowns ───────────────────────────
def period_windows(sunrise_h: float, sunset_h: float) -> dict[str, tuple[int, int]]:
    """Inclusive local-hour windows for the three hunt periods, anchored to the day's
    sunrise/sunset. Midday spans whatever is left between morning and evening so no
    hour is stranded outside all three; on a very short day its range can be empty
    (lo > hi), which is correct. One definition shared by the day ranking, the map,
    and trail-camera period matching so they can never disagree."""
    morning_end = int(sunrise_h + 3)
    evening_start = int(sunset_h - 3)
    return {
        "morning": (int(sunrise_h - 1), morning_end),
        "midday": (morning_end + 1, evening_start - 1),
        "evening": (evening_start, int(sunset_h)),
    }


DEFAULT_PERIOD_WINDOWS = period_windows(6.5, 19.0)


def period_for_hour(hour_of_day: int, windows: dict | None = None) -> str | None:
    for p, (lo, hi) in (windows or DEFAULT_PERIOD_WINDOWS).items():
        if lo <= hour_of_day <= hi:
            return p
    return None


# The species classifier's exact label for white-tailed deer (DFNE's CLASS_NAMES
# in PytorchWildlife). Sightings are matched case-insensitively against this.
DEER_SPECIES = "white-tailed deer"


def _is_deer_sighting(s: dict) -> bool:
    """True if this sighting's species is confirmed deer. A sighting with no
    species (detector ran in fallback mode, or a detection error) is treated
    as unknown, NOT deer — species classification must positively confirm deer
    before it counts toward the boost."""
    species = s.get("species")
    return bool(species) and str(species).strip().lower() == DEER_SPECIES


# Default confidence-weighted evidence sum (see `saturation` below) at which the
# boost reaches its cap. Exposed as the "camera_boost_saturation" setting.
CAMERA_BOOST_SATURATION_DEFAULT = 3.0

# Share of a stand's score kept when scent blows straight at the expected deer approach.
# Exposed as the "scent_gate_floor" setting.
SCENT_GATE_FLOOR_DEFAULT = 0.4


def camera_boost(period: str, sightings: list[dict], max_boost_pct: float,
                 utc_offset_seconds: int = 0, has_camera: bool = False,
                 max_penalty_pct: float = 0.0, lookback_hours: float = 72.0,
                 camera_ready: bool = True, camera_healthy: bool = True,
                 unhealthy_reason: str | None = None,
                 saturation: float = CAMERA_BOOST_SATURATION_DEFAULT,
                 windows: dict | None = None) -> dict:
    """
    Given a stand's recent camera sightings (each a dict with 'timestamp' ISO,
    'confidence_score', and optionally 'species'), return a multiplier and a
    breakdown of trail-camera evidence for this hunt period.

    - No camera on this stand (has_camera=False): always neutral (1.0x) —
      camera evidence never boosts OR penalizes a stand with no camera.
    - Camera present, qualifying deer photos found in this period within the
      last `lookback_hours`: positive boost, scaling with count/confidence
      (diminishing returns) and capped at max_boost_pct. This applies
      regardless of camera_healthy — a real deer photo counts even from a
      camera that's since gone offline or hit its quota. Each qualifying
      sighting contributes its confidence score (0.1-1.0) to a running sum;
      once that sum reaches `saturation`, the boost is fully at max_boost_pct
      — e.g. with the default saturation of 3.0, ~3 high-confidence photos
      (or more, lower-confidence ones) reach the cap.
    - Camera present, ZERO qualifying deer photos in this period, the camera
      has been in place at least `lookback_hours` (camera_ready=True), AND the
      camera looks healthy (camera_healthy=True): negative penalty, capped at
      max_penalty_pct — this camera has had a fair chance to see deer at this
      time of day and hasn't.
    - Camera present but camera_healthy=False (hasn't checked in recently, or
      has hit its photo quota): the absence of photos is not meaningful — the
      camera may simply be unable to capture/transmit anything right now.
      Neutral, no penalty (status "unhealthy" so the UI can still flag it).
    - Camera present but camera_ready=False (still within its grace period
      since being assigned): neutral — not enough time has passed yet for an
      absence of photos to mean anything.

    A "qualifying" sighting is within the last `lookback_hours`, has a LOCAL
    hour-of-day inside the current hunt period (`windows`, the same sunrise/sunset-
    anchored windows the day ranking uses), and is confirmed white-tailed deer.
    The daylight requirement is enforced by the caller (forecast.service filters
    sightings to those taken between sunrise and sunset of their own date).

    utc_offset_seconds is taken from the Open-Meteo forecast for the property's
    location and is used to convert the stored UTC timestamps to local time before
    period matching — without this, a UTC-5 property's 6 AM sighting (stored as
    11:00 UTC) would be misclassified as "midday" instead of "morning".
    """
    if not has_camera:
        return {"multiplier": 1.0, "boost_pct": 0.0, "count": 0, "status": "none",
                "text": "no camera on this stand"}

    now = _dt.datetime.now(_dt.timezone.utc)
    qualifying = []
    for s in sightings or []:
        if not _is_deer_sighting(s):
            continue
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
        if age_h < 0 or age_h > lookback_hours:
            continue
        # Convert UTC → local time using the property's UTC offset before period
        # matching. timedelta handles sub-hour offsets (e.g. India UTC+5:30) correctly.
        local_dt = t + _dt.timedelta(seconds=utc_offset_seconds)
        if period_for_hour(local_dt.hour, windows) != period:
            continue
        qualifying.append(s)

    if qualifying:
        # Accumulate confidence with diminishing returns; `saturation` solid-confidence
        # sightings' worth of evidence reaches the cap.
        accum = 0.0
        for s in qualifying:
            raw_conf = s.get("confidence_score")
            conf = max(0.1, min(1.0, float(raw_conf) if raw_conf is not None else 0.5))
            accum += conf
        sat = saturation if saturation and saturation > 0 else CAMERA_BOOST_SATURATION_DEFAULT
        frac = min(1.0, accum / sat)
        boost_pct = round(max_boost_pct * frac, 1)
        mult = 1.0 + boost_pct / 100.0
        return {
            "multiplier": mult, "boost_pct": boost_pct, "count": len(qualifying), "status": "boost",
            "text": f"+{boost_pct}% from {len(qualifying)} daylight deer photo(s) in the last {int(lookback_hours)}h",
        }

    if camera_ready and max_penalty_pct > 0:
        if not camera_healthy:
            return {
                "multiplier": 1.0, "boost_pct": 0.0, "count": 0, "status": "unhealthy",
                "text": unhealthy_reason or "camera health unknown — penalty skipped",
            }
        penalty_pct = round(max_penalty_pct, 1)
        mult = max(0.05, 1.0 - penalty_pct / 100.0)
        return {
            "multiplier": mult, "boost_pct": -penalty_pct, "count": 0, "status": "penalty",
            "text": f"-{penalty_pct}% — no daylight deer photos from this camera in the last {int(lookback_hours)}h",
        }

    return {
        "multiplier": 1.0, "boost_pct": 0.0, "count": 0, "status": "none",
        "text": "camera in grace period — not enough history yet" if not camera_ready
                else "no recent daylight deer photos",
    }


def score_with_breakdown(stand: dict, hour: dict, period: str | None = None,
                         sightings: list[dict] | None = None, max_boost_pct: float = 0.0,
                         max_penalty_pct: float = 0.0, lookback_hours: float = 72.0,
                         has_camera: bool = False, camera_ready: bool = True,
                         camera_healthy: bool = True, unhealthy_reason: str | None = None,
                         proximity: dict | None = None,
                         utc_offset_seconds: int = 0,
                         thermal_params: dict | None = None,
                         camera_boost_saturation: float = CAMERA_BOOST_SATURATION_DEFAULT,
                         scent_gate_floor: float = SCENT_GATE_FLOOR_DEFAULT,
                         windows: dict | None = None) -> dict:
    """The one stand-scoring function every ranking endpoint uses, so a stand's score
    for a given hour is identical on the map, in sit rankings and in the day view:

        final = (conditions + proximity) × camera × (floor + (1 − floor) × scent)

    conditions  wind steadiness + thermal predictability (0..1)
    proximity   bounded, season-weighted corridor/food/bedding/sign bonus
    camera      trail-camera boost/penalty multiplier for this hunt period
    scent gate  SOFT: bad scent scales the score down to `scent_gate_floor` of its
                value (default 0.4) but never zeroes it; set the floor to 0 for a hard gate.
    """
    base = score_stand_hour(stand, hour, thermal_params)
    breakdown = []

    breakdown.append({"factor": "Wind steadiness", "value": base["steadiness"],
                      "text": f"speed {hour['wind_speed']} mph, gust steadiness {base['steadiness']:.2f}"})
    if base["thermal_known"]:
        breakdown.append({"factor": "Terrain / thermals", "value": 1.0 if base["thermal_phase"] != "neutral" else 0.3,
                          "text": f"thermals {base['thermal_phase']}, drainage {base['drainage_deg']}°"})
    else:
        breakdown.append({"factor": "Terrain / thermals", "value": 0.0,
                          "text": "no usable terrain analysis for this stand — thermals ignored, scent follows the wind"})

    temp_swing = hour.get("temp_swing")
    if temp_swing is not None and temp_swing > 12:
        swing_f = round(temp_swing * 9 / 5, 1)
        swing_c = round(temp_swing, 1)
        strength = "very strong" if temp_swing > 20 else "strong"
        breakdown.append({
            "factor": "Temperature swing",
            "value": min(1.0, (temp_swing - 12) / 12),
            "text": f"{strength} day/night swing ({swing_f} °F / {swing_c} °C) — denser cold air amplifies thermal drainage"
        })

    total = base["conditions"]
    prox_total = float(proximity.get("total") or 0.0) if proximity else 0.0
    if prox_total > 0:
        total += prox_total
        season = proximity.get("season_phase")
        breakdown.append({"factor": "Infrastructure proximity", "value": round(prox_total, 3),
                          "text": f"+{round(prox_total*100)} from nearby corridor/food/bedding/sign"
                                  + (f", weighted for {season}" if season else "")})

    cam = {"multiplier": 1.0, "boost_pct": 0.0, "count": 0, "status": "none", "text": "camera scoring off"}
    if period and (max_boost_pct or max_penalty_pct):
        cam = camera_boost(period, sightings or [], max_boost_pct, utc_offset_seconds,
                            has_camera=has_camera, max_penalty_pct=max_penalty_pct,
                            lookback_hours=lookback_hours, camera_ready=camera_ready,
                            camera_healthy=camera_healthy, unhealthy_reason=unhealthy_reason,
                            saturation=camera_boost_saturation, windows=windows)
        total *= cam["multiplier"]
        if cam["status"] != "none":
            breakdown.append({"factor": "Trail-camera", "value": cam["boost_pct"] / 100.0,
                              "text": cam["text"]})

    floor = max(0.0, min(1.0, float(scent_gate_floor)))
    scent_multiplier = floor + (1.0 - floor) * base["scent_score"]

    if stand.get("deer_approach_deg") is not None:
        if base["scent_score"] > 0.6:
            stxt = "scent carries away from expected deer approach"
        elif base["scent_score"] > 0.35:
            stxt = "scent crosses the deer approach"
        else:
            stxt = "scent blows toward deer — heavily reduces other factors"
    else:
        stxt = "no deer-approach set; scent direction only"
        
    breakdown.append({"factor": "Scent direction (soft gate)", "value": scent_multiplier,
                      "text": f"{stxt} (blending to {base['scent_to_deg']}°)"})

    total = round(total * scent_multiplier, 3)

    return {
        "final_score": total,
        "base_score": round(base["total"], 3),
        "proximity_bonus": round(prox_total, 3),
        "camera": cam,
        "breakdown": breakdown,
        "scent_to_deg": base["scent_to_deg"],
        "thermal_phase": base["thermal_phase"] if base["thermal_known"] else "unknown",
        "drainage_deg": base["drainage_deg"],
        "scent_score": base["scent_score"],
    }