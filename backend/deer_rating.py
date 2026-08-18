"""
Deer day-rating model — produces a 1-5 "deer movement" rating optimized for
DAYTIME (huntable) movement.

Design grounded in the peer-reviewed findings the user supplied plus AGFC /
Wilson & Sealander Arkansas reproduction data:

  • Rut/season is the dominant biological driver (partial-migration & fractal-path
    studies): bucks travel far more, and more in daylight, pre-rut → peak.
    Modeled as a multiplier, not just an additive term.
  • Barometric pressure: high and/or rapidly-changing pressure correlates with
    daylight movement spikes (EKU Taylor Fork). Sweet spot ~30.0–30.4 inHg.
  • Wind: moderate wind INCREASES daytime buck movement (scenting efficiency);
    very high wind and dead calm are mildly suppressive. Modeled as a curve that
    peaks at moderate speed — NOT "more wind = worse."
  • Heavy rain: strong suppressor of movement across both sexes; partially blunted
    when paired with high wind.
  • Temperature: does NOT change distance traveled, it shifts WHEN deer move.
    Cooler-than-recent (a front) pushes movement into daylight (good for hunting);
    warmer-than-recent shifts it to night (bad). Modeled as a daytime-shift term
    keyed to departure from the trailing baseline, not absolute temperature.
  • Moon phase: DELIBERATELY EXCLUDED. MSU "Lunar Legends" found no statistically
    significant effect on buck activity.

Pressure is provided by Open-Meteo in hPa; we convert to inHg for thresholds.
"""
from __future__ import annotations
from datetime import date, datetime


HPA_TO_INHG = 0.02953

# ── rut calendar (central Arkansas; tunable later) ───────────────────────────
# Wilson & Sealander / AGFC: north AR peak ~Nov 13, central peak ~Dec 5,
# east/south ~Dec 14. Best DAYLIGHT cruising is the ~2-3 weeks BEFORE peak.
# We model an intensity 0..1 over day-of-year with a pre-rut seeking plateau
# that is excellent for daylight hunting, then the breeding peak.
RUT_PEAK_MONTH = 12
RUT_PEAK_DAY = 5

# ── combine ──────────────────────────────────────────────────────────────────
# Weather produces a 0..1 index; rut multiplies it (biology outranks weather),
# but a strong rut also lifts the floor (deer move even in poor weather).
WEATHER_WEIGHTS = {
    "pressure": 0.32,
    "wind": 0.20,
    "rain": 0.28,
    "temp": 0.20,
}


def _doy(d: date) -> int:
    return d.timetuple().tm_yday


def rut_intensity(d: date, peak_month: int = RUT_PEAK_MONTH, peak_day: int = RUT_PEAK_DAY) -> tuple[float, str]:
    """Return (0..1 intensity, phase label) for daytime-huntable rut activity.
    Peaks slightly BEFORE breeding peak (seeking/chasing = best daylight movement).
    peak_month/peak_day are configurable (regional rut timing)."""
    try:
        peak = date(d.year, int(peak_month), int(peak_day))
    except ValueError:
        peak = date(d.year, RUT_PEAK_MONTH, RUT_PEAK_DAY)
    # huntable daylight movement peaks ~10 days before breeding peak (chasing phase)
    hunt_peak_doy = _doy(peak) - 10
    delta = _doy(d) - hunt_peak_doy  # days from the daylight-movement peak

    # piecewise: ramp up through October, plateau pre-rut, taper post-breeding
    if delta < -45:        # before ~early Oct: low background
        return 0.15, "pre-season"
    if -45 <= delta < -18:  # ramp (early Oct → late Oct)
        return 0.15 + 0.45 * (delta + 45) / 27, "early season"
    if -18 <= delta <= 10:  # seeking / chasing plateau — best daylight window
        return 0.9 + 0.1 * (1 - abs(delta) / 18), "rut (seeking/chasing)"
    if 10 < delta <= 28:    # breeding peak / lockdown — great movement, less daylight
        return 0.85 - 0.25 * (delta - 10) / 18, "peak breeding / lockdown"
    if 28 < delta <= 55:    # post-rut tail / second rut bump
        return 0.6 - 0.3 * (delta - 28) / 27, "post-rut"
    return 0.2, "off-season"


# ── individual weather factors, each returns 0..1 (higher = more daytime movement)

def pressure_factor(inhg: float | None, trend_inhg_per_3h: float | None) -> float:
    if inhg is None:
        return 0.5
    # absolute: sweet spot 30.0–30.4, fall off outside
    if inhg >= 30.0:
        abs_f = max(0.4, 1 - (inhg - 30.2) ** 2 / 0.5) if inhg <= 30.6 else 0.55
        abs_f = min(1.0, abs_f)
    else:
        abs_f = max(0.25, 1 - (30.0 - inhg) / 0.8)  # low pressure → less daytime movement
    # rapid change (front) adds a daylight spike, magnitude of change matters either sign
    trend_f = 0.0
    if trend_inhg_per_3h is not None:
        trend_f = min(0.25, abs(trend_inhg_per_3h) / 0.06 * 0.25)
    return max(0.0, min(1.0, abs_f * 0.8 + trend_f + 0.0))


def wind_factor(mph: float | None) -> float:
    """Peaks at moderate wind (~5–15 mph), falls off at calm and high extremes."""
    if mph is None:
        return 0.5
    if mph < 3:
        return 0.55              # dead calm: mildly suppressive
    if mph <= 15:
        return 0.75 + 0.25 * (1 - abs(mph - 9) / 6)   # peak around 9 mph
    if mph <= 25:
        return 0.75 - 0.35 * (mph - 15) / 10
    return 0.35                  # very high wind: suppressive (esp. for daytime calm-seekers)


def rain_factor(mm: float | None, wind_mph: float | None) -> float:
    """Heavy rain strongly suppresses; high wind partially blunts the suppression."""
    if mm is None or mm <= 0.2:
        return 1.0
    if mm < 2.5:
        supp = 0.85              # light rain / drizzle: minor
    elif mm < 7.5:
        supp = 0.55              # moderate
    else:
        supp = 0.25              # heavy: strong suppression
    if wind_mph and wind_mph > 12:
        supp = min(1.0, supp + 0.15)   # wind blunts the suppression slightly
    return supp


def temp_shift_factor(day_high_f: float | None, baseline_f: float | None) -> float:
    """Daytime-movement shift from temperature DEPARTURE vs recent baseline.
    Cooler than recent (a front) → more daytime movement; warmer → less."""
    if day_high_f is None or baseline_f is None:
        return 0.6
    dep = day_high_f - baseline_f   # negative = colder than recent = good for daylight
    if dep <= -15:
        return 1.0
    if dep <= 0:
        return 0.65 + 0.35 * (-dep / 15)
    if dep <= 15:
        return 0.65 - 0.4 * (dep / 15)   # warm spell → night movement
    return 0.25


def rate_day(d: date, wx: dict, weights: dict | None = None,
             rut_peak_month: int = RUT_PEAK_MONTH, rut_peak_day: int = RUT_PEAK_DAY) -> dict:
    """wx keys (daytime aggregates):
        pressure_inhg, pressure_trend_inhg, wind_mph, rain_mm, day_high_f, baseline_f
    weights: optional {pressure,wind,rain,temp} relative weights (any scale; they
        are normalized to sum to 1 so the score stays calibrated 0-1).
    rut_peak_month/day: configurable regional breeding peak.
    Returns {rating 1-5, score 0-1, rut:{...}, factors:{...}, breakdown:[...]}.
    """
    w = dict(WEATHER_WEIGHTS)
    if weights:
        for k in ("pressure", "wind", "rain", "temp"):
            if weights.get(k) is not None:
                w[k] = max(0.0, float(weights[k]))
    tot = w["pressure"] + w["wind"] + w["rain"] + w["temp"]
    if tot <= 0:
        w = dict(WEATHER_WEIGHTS); tot = 1.0
    w = {k: v / tot for k, v in w.items()}

    pf = pressure_factor(wx.get("pressure_inhg"), wx.get("pressure_trend_inhg"))
    wf = wind_factor(wx.get("wind_mph"))
    rf = rain_factor(wx.get("rain_mm"), wx.get("wind_mph"))
    tf = temp_shift_factor(wx.get("day_high_f"), wx.get("baseline_f"))

    weather = (pf * w["pressure"] + wf * w["wind"] + rf * w["rain"] + tf * w["temp"])

    rut, phase = rut_intensity(d, rut_peak_month, rut_peak_day)

    # rut multiplier: scales weather and lifts a floor so a hot rut day still
    # rates decently in mediocre weather — but heavy rain / warm spells can still
    # pull it down (the research treats heavy rain as a strong suppressor).
    # Floor is modest so weather retains real influence even during the rut.
    floor = 0.42 * rut
    # When the rut is weak, even perfect weather shouldn't read as a heavy day:
    # cap the weather-only contribution so off-season tops out around 3🦌.
    rut_gain = 0.45 + 0.95 * rut
    score = max(weather * rut_gain, floor)
    # extra: a genuinely bad-weather day (heavy rain) suppresses regardless of rut
    if wx.get("rain_mm") and wx["rain_mm"] >= 7.5:
        score = min(score, 0.55)
    score = max(0.0, min(1.0, score))

    rating = 1 + round(score * 4)   # 1..5
    rating = max(1, min(5, rating))

    def _lbl(v):
        return "strong" if v >= 0.75 else "moderate" if v >= 0.5 else "weak"

    breakdown = [
        {"factor": "Rut / season", "value": round(rut, 2),
         "impact": f"{phase} — {_lbl(rut)} seasonal drive", "weight": "multiplier"},
        {"factor": "Barometric pressure", "value": round(pf, 2),
         "impact": f"{_lbl(pf)} ({wx.get('pressure_inhg')}\" )", "weight": round(w['pressure'], 2)},
        {"factor": "Wind", "value": round(wf, 2),
         "impact": f"{_lbl(wf)} ({wx.get('wind_mph')} mph)", "weight": round(w['wind'], 2)},
        {"factor": "Rain", "value": round(rf, 2),
         "impact": f"{_lbl(rf)} suppression ({wx.get('rain_mm')} mm)", "weight": round(w['rain'], 2)},
        {"factor": "Temperature shift", "value": round(tf, 2),
         "impact": f"{_lbl(tf)} daytime shift ({wx.get('day_high_f')}°F vs {wx.get('baseline_f')}°F baseline)",
         "weight": round(w['temp'], 2)},
    ]

    return {
        "rating": rating,
        "score": round(score, 3),
        "rut": {"intensity": round(rut, 2), "phase": phase},
        "factors": {
            "pressure": round(pf, 2),
            "wind": round(wf, 2),
            "rain": round(rf, 2),
            "temp_shift": round(tf, 2),
            "weather_index": round(weather, 2),
        },
        "breakdown": breakdown,
        "inputs": wx,
    }
