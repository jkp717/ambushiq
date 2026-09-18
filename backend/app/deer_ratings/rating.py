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
import calendar
from datetime import date, datetime, timedelta
import math


HPA_TO_INHG = 0.02953

# ── rut calendar (central Arkansas; tunable later) ───────────────────────────
# Wilson & Sealander / AGFC: north AR peak ~Nov 13, central peak ~Dec 5,
# east/south ~Dec 14. Best DAYLIGHT cruising is the ~2-3 weeks BEFORE peak.
# We model an intensity 0..1 over day-of-year with a pre-rut seeking plateau
# that is excellent for daylight hunting, then the breeding peak.
RUT_PEAK_MONTH = 12
RUT_PEAK_DAY = 5

# ── combine ──────────────────────────────────────────────────────────────────
# Weather produces a 0..1 index; rut multiplies it (biology outranks weather):
# score = weather * (RUT_GAIN_BASE + RUT_GAIN_SLOPE * rut).
RUT_GAIN_BASE = 0.35
RUT_GAIN_SLOPE = 0.65
WEATHER_WEIGHTS = {
    "pressure": 0.32,
    "wind": 0.20,
    "rain": 0.28,
    "temp": 0.20,
}


def _doy(d: date) -> int:
    return d.timetuple().tm_yday


def _peak_date(year: int, month: int, day: int) -> date:
    """The rut peak in `year`, clamping an impossible day (e.g. Feb 29 in a common year)."""
    month = month if 1 <= month <= 12 else RUT_PEAK_MONTH
    return date(year, month, max(1, min(day, calendar.monthrange(year, month)[1])))


def rut_intensity(d: date, peak_month: int = RUT_PEAK_MONTH, peak_day: int = RUT_PEAK_DAY) -> tuple[float, str]:
    """Continuous curve for daytime-huntable rut activity, plus a phase label.

    The day offset is measured against the NEAREST occurrence of the peak (previous,
    current or next year), so regions whose peak falls in Jan-May work the same as
    Nov/Dec ones. Intensity is an asymmetric Gaussian around the "hunt peak" (10 days
    before the breeding peak) with a small dip during the breeding-peak lockdown, when
    bucks are tied to does and daytime cruising drops off."""
    pm, pd = int(peak_month), int(peak_day)
    delta = min(((d - (_peak_date(y, pm, pd) - timedelta(days=10))).days
                 for y in (d.year - 1, d.year, d.year + 1)), key=abs)

    # Asymmetric Gaussian: ramps up over ~22 days, tapers off slower over ~32 days
    baseline = 0.15
    if delta < 0:
        intensity = baseline + 0.85 * math.exp(-0.5 * (delta / 22.0) ** 2)
    else:
        intensity = baseline + 0.85 * math.exp(-0.5 * (delta / 32.0) ** 2)
    intensity -= 0.20 * math.exp(-0.5 * ((delta - 10) / 5.0) ** 2)   # lockdown dip

    # Boundaries chosen so "pre-season"/"off-season" only label days with intensity < ~0.3
    if delta < -41: phase = "pre-season"
    elif delta < -10: phase = "early season / seeking"
    elif delta <= 5: phase = "rut (chasing / peak daylight)"
    elif delta <= 20: phase = "breeding peak / lockdown"
    elif delta <= 60: phase = "post-rut"
    else: phase = "off-season"

    return min(1.0, max(0.15, intensity)), phase


# ── individual weather factors, each returns 0..1 (higher = more daytime movement)

def _interp(x: float, points: list[tuple[float, float]]) -> float:
    """Piecewise-linear interpolation through sorted (x, y) anchors, clamped at both ends.
    Used for every factor curve so no factor has a step at a branch boundary."""
    if x <= points[0][0]:
        return points[0][1]
    for (x0, y0), (x1, y1) in zip(points, points[1:]):
        if x <= x1:
            return y0 + (y1 - y0) * (x - x0) / (x1 - x0)
    return points[-1][1]


# Sea-level pressure (inHg) → absolute-pressure factor: flat sweet spot 30.0–30.4,
# lower pressure → less daytime movement, very high pressure → mildly less.
_PRESSURE_CURVE = [(29.4, 0.25), (30.0, 1.0), (30.4, 1.0), (30.8, 0.55)]
# Wind (mph) → factor: peaks around 9 mph, dead calm and gales are suppressive.
_WIND_CURVE = [(0.0, 0.55), (3.0, 0.75), (9.0, 1.0), (15.0, 0.75), (25.0, 0.40), (35.0, 0.35)]
# Daytime rain (mm) → factor: light/moderate/heavy suppression ramps.
_RAIN_CURVE = [(0.2, 1.0), (2.5, 0.80), (5.0, 0.55), (7.5, 0.30), (12.0, 0.25)]
HEAVY_RAIN_MM = 7.5
HEAVY_RAIN_SCORE_CAP = 0.35


def pressure_factor(inhg: float | None, trend_inhg_per_3h: float | None) -> float:
    if inhg is None:
        return 0.5
    abs_f = _interp(inhg, _PRESSURE_CURVE)
    # rapid change (front) adds a daylight spike, magnitude of change matters either sign
    trend_f = 0.0
    if trend_inhg_per_3h is not None:
        trend_f = min(0.25, abs(trend_inhg_per_3h) / 0.06 * 0.25)
    return max(0.0, min(1.0, abs_f * 0.8 + trend_f))


def wind_factor(mph: float | None) -> float:
    """Peaks at moderate wind (~5–15 mph), falls off at calm and high extremes."""
    if mph is None:
        return 0.5
    return _interp(mph, _WIND_CURVE)


def rain_factor(mm: float | None, wind_mph: float | None) -> float:
    """Heavy rain strongly suppresses; high wind partially blunts the suppression."""
    if mm is None or mm <= 0.2:
        return 1.0
    supp = _interp(mm, _RAIN_CURVE)
    if wind_mph:
        # blunting ramps in smoothly between 8 and 16 mph rather than switching on at 12
        supp = min(1.0, supp + 0.15 * max(0.0, min(1.0, (wind_mph - 8.0) / 8.0)))
    return supp


def temp_shift_factor(day_high_f: float | None, baseline_f: float | None, dew_point_f: float | None = None) -> float:
    """Daytime-movement shift from temp departure, penalized by high dew points."""
    if day_high_f is None or baseline_f is None:
        return 0.6
    
    dep = day_high_f - baseline_f 
    if dep <= -15: base_f = 1.0
    elif dep <= 0: base_f = 0.65 + 0.35 * (-dep / 15)
    elif dep <= 15: base_f = 0.65 - 0.4 * (dep / 15) 
    else: base_f = 0.25

    # High humidity creates stifling conditions that suppress movement
    if dew_point_f and dew_point_f >= 60.0:
        suppression = min(0.3, (dew_point_f - 60) * 0.02)
        base_f = max(0.15, base_f - suppression)

    return base_f


def rating_from_score(score: float) -> int:
    """0.2-wide bins — [0,.2)=1 … [.8,1]=5 — instead of round(), which is banker's
    rounding in Python and rated 0.375 and 0.625 both as 3."""
    return 1 + min(4, max(0, int(score * 5)))


def rate_day(d: date, wx: dict, weights: dict | None = None,
             rut_peak_month: int = RUT_PEAK_MONTH, rut_peak_day: int = RUT_PEAK_DAY) -> dict:
    """wx keys (daytime aggregates):
        pressure_inhg, pressure_hpa, pressure_trend_inhg, pressure_trend_hpa, wind_mph, rain_mm, day_high_f, baseline_f
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

    # Handle pressure input conversion (supporting both explicit inHg or hPa from Open-Meteo)
    raw_p = wx.get("pressure_inhg")
    if raw_p is None and wx.get("pressure_hpa") is not None:
        raw_p = wx["pressure_hpa"] * HPA_TO_INHG
    elif raw_p is None and wx.get("pressure") is not None:
        # Fallback generic 'pressure' key check (if > 100 assume hPa, else inHg)
        val = wx["pressure"]
        raw_p = val * HPA_TO_INHG if val > 100 else val

    raw_trend = wx.get("pressure_trend_inhg")
    if raw_trend is None and wx.get("pressure_trend_hpa") is not None:
        raw_trend = wx["pressure_trend_hpa"] * HPA_TO_INHG

    pf = pressure_factor(raw_p, raw_trend)
    wf = wind_factor(wx.get("wind_mph"))
    rf = rain_factor(wx.get("rain_mm"), wx.get("wind_mph"))
    tf = temp_shift_factor(wx.get("day_high_f"), wx.get("baseline_f"), wx.get("dew_point_f"))

    weather = (pf * w["pressure"] + wf * w["wind"] + rf * w["rain"] + tf * w["temp"])

    rut, phase = rut_intensity(d, rut_peak_month, rut_peak_day)

    # Season is a pure multiplier on weather: perfect weather at peak rut reaches 1.0,
    # perfect weather in the off-season (rut≈0.15) reaches ~0.45 (a 3), and terrible
    # weather bottoms out near 0.1-0.25 in either season, so the whole 1-5 scale is
    # reachable. There is deliberately no additive floor — it compressed the range.
    rut_gain = RUT_GAIN_BASE + RUT_GAIN_SLOPE * rut
    score = weather * rut_gain
    # a genuinely heavy-rain day suppresses regardless of rut
    if wx.get("rain_mm") and wx["rain_mm"] >= HEAVY_RAIN_MM:
        score = min(score, HEAVY_RAIN_SCORE_CAP)
    score = max(0.0, min(1.0, score))

    rating = rating_from_score(score)

    def _lbl(v):
        return "strong" if v >= 0.75 else "moderate" if v >= 0.5 else "weak"

    def _supp_lbl(v):
        """v = amount of suppression (0 = dry/no effect, up to ~0.75 = heavy rain).
        Rain's own factor is a favorability score (1.0 = dry = no suppression), so this
        takes 1-rf rather than reusing _lbl directly on rf — labeling rf itself would
        call a dry day (rf=1.0) "strong suppression", which is backwards."""
        if v <= 0.05: return "no"
        if v >= 0.6: return "strong"
        if v >= 0.3: return "moderate"
        return "slight"

    breakdown = [
        {"factor": "Rut / season", "value": round(rut, 2),
         "impact": f"{phase}, {_lbl(rut)} seasonal drive", "weight": "multiplier"},
        {"factor": "Barometric pressure", "value": round(pf, 2),
         "impact": f"{_lbl(pf)} ({round(raw_p, 2) if raw_p else 'N/A'}\" )", "weight": round(w['pressure'], 2)},
        {"factor": "Wind", "value": round(wf, 2),
         "impact": f"{_lbl(wf)} ({wx.get('wind_mph')} mph)", "weight": round(w['wind'], 2)},
        {"factor": "Rain", "value": round(rf, 2),
         "impact": f"{_supp_lbl(1 - rf)} suppression ({wx.get('rain_mm')} mm)", "weight": round(w['rain'], 2)},
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