"""Composite candidate scoring, clustering, overlap math, and reasoning text.

Reuses the app's real proximity scoring (proximity_bonus) instead of reinventing
it, and adds a camera-confirmation bonus and an "unexplored ground" bias so
suggestions favor spots the user hasn't already marked, not just ones that
reflect data they already have.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone

from app.forecast.scoring import _is_deer_sighting
from app.forecast.service import _haversine_m, _point_to_segment_m, proximity_bonus

# Internal normalization constants — not user-exposed settings, since these are
# reference scales for combining already-exposed factors, not independent knobs.
W_SADDLE_VS_EDGE = 0.6          # terrain_raw = 0.6*funnel + 0.4*edge
CAMERA_CONFIRM_RADIUS_M = 200.0
CAMERA_CONFIRM_LOOKBACK_HOURS = 24 * 14.0  # 2 weeks — broader than a single hunt period
UNEXPLORED_FULL_CREDIT_M = 400.0

_PROXIMITY_WEIGHT_DEFAULTS = {"weight_corridor": 0.15, "weight_food": 0.15, "weight_bedding": 0.10,
                              "weight_scrape": 0.12, "weight_rub": 0.10}


def proximity_norm_cap(settings: dict) -> float:
    """Reference scale for normalizing proximity_bonus().total to 0..1: the sum of the
    configured per-type weights — what a stand sitting on one ideal feature of every
    type would score. Tracks the user's tuning instead of assuming the default weights."""
    return max(1e-9, sum(float(settings.get(k, d)) for k, d in _PROXIMITY_WEIGHT_DEFAULTS.items()))


def camera_confirmation_bonus(lat: float, lon: float, camera_sightings: list[dict]) -> float:
    """Modeled on forecast/scoring.py's camera_boost() confidence-accumulation-with-
    saturation shape, but distance-weighted instead of period-matched — a scouting
    suggestion isn't tied to one hunt hour. camera_sightings entries are pre-joined
    to {lat, lon, timestamp, confidence_score, species} in service.py."""
    if not camera_sightings:
        return 0.0
    now = datetime.now(timezone.utc)
    accum = 0.0
    for sg in camera_sightings:
        if not _is_deer_sighting(sg):
            continue
        try:
            t = datetime.fromisoformat(str(sg["timestamp"]).replace("Z", "+00:00"))
        except (KeyError, ValueError, TypeError):
            continue
        age_h = (now - t).total_seconds() / 3600
        if age_h < 0 or age_h > CAMERA_CONFIRM_LOOKBACK_HOURS:
            continue
        d = _haversine_m(lat, lon, sg["lat"], sg["lon"])
        if d > CAMERA_CONFIRM_RADIUS_M:
            continue
        raw_conf = sg.get("confidence_score")
        conf = max(0.1, min(1.0, 0.5 if raw_conf is None else float(raw_conf)))
        dist_weight = max(0.0, 1 - d / CAMERA_CONFIRM_RADIUS_M)
        accum += conf * dist_weight
    saturation = 3.0
    return min(1.0, accum / saturation)


def unexplored_bonus(lat: float, lon: float, known_points: list[tuple[float, float]]) -> float:
    """Distance to the nearest existing stand/camera/suggestion, normalized over a
    fixed reference distance — the concrete implementation of favoring ground the
    user hasn't already marked."""
    if not known_points:
        return 1.0
    nearest = min(_haversine_m(lat, lon, p[0], p[1]) for p in known_points)
    return min(1.0, nearest / UNEXPLORED_FULL_CREDIT_M)


def circle_overlap_area(d: float, r1: float, r2: float) -> float:
    """Standard circle-circle lens-intersection area."""
    if d >= r1 + r2:
        return 0.0
    if d <= abs(r1 - r2):
        return math.pi * min(r1, r2) ** 2
    r1sq, r2sq = r1 ** 2, r2 ** 2
    alpha = math.acos(max(-1.0, min(1.0, (d ** 2 + r1sq - r2sq) / (2 * d * r1))))
    beta = math.acos(max(-1.0, min(1.0, (d ** 2 + r2sq - r1sq) / (2 * d * r2))))
    return r1sq * (alpha - math.sin(2 * alpha) / 2) + r2sq * (beta - math.sin(2 * beta) / 2)


def _nearest_feature_text(lat: float, lon: float, zones: list[dict], corridors: list[dict],
                           sign: list[dict]) -> str | None:
    best: tuple[float, str] | None = None
    for z in zones:
        d = max(0.0, _haversine_m(lat, lon, z["lat"], z["lon"]) - (z.get("radius_m") or 0))
        label = "a food zone" if z.get("kind") == "food" else "a bedding zone"
        if best is None or d < best[0]:
            best = (d, f"near {label}")
    for sg in sign:
        d = _haversine_m(lat, lon, sg["lat"], sg["lon"])
        label = "a rub" if sg.get("kind") == "rub" else "a scrape"
        if best is None or d < best[0]:
            best = (d, f"{round(d)}m from {label}")
    for cr in corridors:
        pts = cr.get("points") or []
        if len(pts) < 2:
            continue
        dmin = min(
            _point_to_segment_m(lat, lon, pts[i][0], pts[i][1], pts[i + 1][0], pts[i + 1][1])
            for i in range(len(pts) - 1)
        )
        if best is None or dmin < best[0]:
            best = (dmin, "close to a known deer corridor")
    return best[1] if best else None


def build_breakdown(funnel_val: float, edge_val: float, prox_norm: float, camera_val: float,
                     unexplored_val: float, lat: float, lon: float, zones: list[dict],
                     corridors: list[dict], sign: list[dict]) -> list[dict]:
    items = []
    if funnel_val > 0.4:
        items.append({"factor": "terrain_funnel", "value": funnel_val,
                       "text": "sits on a terrain saddle or pinch point"})
    if edge_val > 0.4:
        items.append({"factor": "habitat_edge", "value": edge_val,
                       "text": "near a forest/field edge"})
    if prox_norm > 0.15:
        text = _nearest_feature_text(lat, lon, zones, corridors, sign)
        if text:
            items.append({"factor": "proximity", "value": prox_norm, "text": text})
    if camera_val > 0.15:
        items.append({"factor": "camera_confirmation", "value": camera_val,
                       "text": "confirmed by recent trail-camera deer activity nearby"})
    if unexplored_val > 0.6:
        items.append({"factor": "unexplored", "value": unexplored_val,
                       "text": "in ground you haven't marked yet"})
    items.sort(key=lambda x: -x["value"])
    return items


def render_reasoning(breakdown: list[dict]) -> str:
    if not breakdown:
        return "Flagged by the terrain model with no single standout factor."
    parts = [b["text"] for b in breakdown[:3]]
    sentence = "; ".join(parts)
    return sentence[0].upper() + sentence[1:] + "."


def score_grid(lats, lons, funnel_grid, edge_grid, zones: list[dict], corridors: list[dict],
               sign: list[dict], settings: dict, camera_sightings: list[dict],
               known_points: list[tuple[float, float]]) -> list[dict]:
    """Score every sample-grid cell, returning candidates above the configured floor."""
    n = len(lats)
    w_t = float(settings.get("scout_weight_terrain", 0.55))
    w_p = float(settings.get("scout_weight_proximity", 0.20))
    w_c = float(settings.get("scout_weight_camera", 0.15))
    w_u = float(settings.get("scout_weight_unexplored", 0.10))
    weight_sum = max(1e-9, w_t + w_p + w_c + w_u)
    min_score = float(settings.get("scout_min_candidate_score", 40.0))
    prox_cap = proximity_norm_cap(settings)

    candidates = []
    for r in range(n):
        for c in range(len(lons)):
            lat, lon = lats[r], lons[c]
            funnel_val = float(funnel_grid[r, c])
            edge_val = float(edge_grid[r, c])
            terrain_raw = W_SADDLE_VS_EDGE * funnel_val + (1 - W_SADDLE_VS_EDGE) * edge_val

            prox_total = proximity_bonus({"lat": lat, "lon": lon}, zones, corridors, settings, sign)["total"]
            prox_norm = min(1.0, prox_total / prox_cap)

            camera_val = camera_confirmation_bonus(lat, lon, camera_sightings)
            unexplored_val = unexplored_bonus(lat, lon, known_points)

            raw = (w_t * terrain_raw + w_p * prox_norm + w_c * camera_val + w_u * unexplored_val) / weight_sum
            score = max(0.0, min(100.0, raw * 100.0))
            if score < min_score:
                continue

            breakdown = build_breakdown(funnel_val, edge_val, prox_norm, camera_val, unexplored_val,
                                         lat, lon, zones, corridors, sign)
            candidates.append({"lat": lat, "lon": lon, "score": score, "breakdown": breakdown})
    return candidates


def cluster_candidates(candidates: list[dict], settings: dict) -> list[dict]:
    """Greedy non-max suppression: highest score first, suppress everything within
    the configured separation radius, repeat until candidates or the per-run cap run out."""
    min_sep = float(settings.get("scout_min_separation_m", 150.0))
    max_n = int(settings.get("scout_max_suggestions_per_run", 8))
    remaining = sorted(candidates, key=lambda c: -c["score"])
    picked: list[dict] = []
    for cand in remaining:
        if len(picked) >= max_n:
            break
        if any(_haversine_m(cand["lat"], cand["lon"], p["lat"], p["lon"]) < min_sep for p in picked):
            continue
        picked.append(cand)
    return picked


def filter_against_existing(candidates: list[dict], existing: list[dict], settings: dict) -> list[dict]:
    """'merge' mode: drop a candidate whose area overlaps an existing suggestion by
    more than the configured fraction of its own area."""
    threshold = float(settings.get("scout_overlap_skip_threshold", 0.5))
    suggestion_radius = float(settings.get("scout_suggestion_radius_m", 60.0))
    own_area = math.pi * suggestion_radius ** 2
    out = []
    for cand in candidates:
        skip = False
        for ex in existing:
            d = _haversine_m(cand["lat"], cand["lon"], ex["lat"], ex["lon"])
            overlap = circle_overlap_area(d, suggestion_radius, ex.get("radius_m") or suggestion_radius)
            if own_area > 0 and overlap / own_area > threshold:
                skip = True
                break
        if not skip:
            out.append(cand)
    return out
