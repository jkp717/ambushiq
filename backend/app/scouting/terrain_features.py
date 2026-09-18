"""Terrain-funnel detection: saddles/cols and pinch points, from a DEM grid.

Pure numpy — no new dependency beyond what stands/terrain.py already uses.
Whitetail deer travel is channeled by terrain funnels: saddles (low points
along a ridge crest) and pinch points (narrow gaps between two steep obstacles).
"""
from __future__ import annotations

import numpy as np

from app.stands.terrain import compute_slope_aspect

_RAY_DIRS = [(-1, 0), (1, 0), (0, -1), (0, 1), (-1, -1), (1, 1), (-1, 1), (1, -1)]
_RAY_PAIRS = [(0, 1), (2, 3), (4, 5), (6, 7)]  # opposite-direction pairs: N/S, W/E, NW/SE, NE/SW
_MAX_PINCH_SEARCH_CELLS = 12


def _smooth3(a: np.ndarray) -> np.ndarray:
    """3x3 box blur via edge-padded shifted-slice averaging (no scipy)."""
    padded = np.pad(a, 1, mode="edge")
    out = np.zeros_like(a)
    for dr in (-1, 0, 1):
        for dc in (-1, 0, 1):
            out += padded[1 + dr:1 + dr + a.shape[0], 1 + dc:1 + dc + a.shape[1]]
    return out / 9.0


def saddle_score_grid(dem: np.ndarray, cell_m: float) -> np.ndarray:
    """A point is a topographic saddle where the Hessian of the (smoothed) elevation
    surface has mixed-sign eigenvalues: det(H) = z_xx*z_yy - z_xy^2 < 0. Returns a
    normalized [0,1] grid, higher = stronger saddle."""
    z = _smooth3(dem)
    zxx = np.zeros_like(z)
    zyy = np.zeros_like(z)
    zxy = np.zeros_like(z)
    zxx[:, 1:-1] = (z[:, 2:] - 2 * z[:, 1:-1] + z[:, :-2]) / cell_m ** 2
    zyy[1:-1, :] = (z[2:, :] - 2 * z[1:-1, :] + z[:-2, :]) / cell_m ** 2
    zxy[1:-1, 1:-1] = (z[2:, 2:] - z[2:, :-2] - z[:-2, 2:] + z[:-2, :-2]) / (4 * cell_m ** 2)
    det_h = zxx * zyy - zxy ** 2
    raw = np.clip(-det_h, 0, None)
    raw[0, :] = raw[-1, :] = raw[:, 0] = raw[:, -1] = 0
    positive = raw[raw > 0]
    cap = np.percentile(positive, 90) if positive.size else 1.0
    return np.clip(raw / max(cap, 1e-9), 0, 1)


def pinch_score_grid(slope_pct: np.ndarray, cell_m: float, steep_slope_pct: float,
                      max_pinch_width_m: float) -> np.ndarray:
    """From each gentle-slope cell, cast rays in 8 directions to find the nearest
    steep-terrain cell in each direction. The narrowest opposite-pair gap under
    max_pinch_width_m scores as a pinch point, scaled by how narrow it is."""
    n = slope_pct.shape[0]
    steep = slope_pct > steep_slope_pct
    out = np.zeros((n, n), dtype=np.float32)
    for r in range(n):
        for c in range(n):
            if steep[r, c]:
                continue
            dists = []
            for dr, dc in _RAY_DIRS:
                d = None
                for step in range(1, _MAX_PINCH_SEARCH_CELLS + 1):
                    rr, cc = r + dr * step, c + dc * step
                    if not (0 <= rr < n and 0 <= cc < n):
                        break
                    if steep[rr, cc]:
                        d = step
                        break
                dists.append(d)
            best_gap = None
            for i, j in _RAY_PAIRS:
                if dists[i] is not None and dists[j] is not None:
                    gap_m = (dists[i] + dists[j]) * cell_m
                    if best_gap is None or gap_m < best_gap:
                        best_gap = gap_m
            if best_gap is not None and best_gap <= max_pinch_width_m:
                out[r, c] = 1.0 - best_gap / max_pinch_width_m
    return out


def funnel_score_grid(dem: np.ndarray, cell_m: float, settings: dict) -> dict:
    _, slope_rda, _ = compute_slope_aspect(dem, cell_m)
    # slope_riserun is already true rise/run (see compute_slope_aspect); ×100 = percent.
    # NoData cells come back negative and are clipped so they never count as "steep".
    slope_pct = np.clip(np.array(slope_rda, dtype=np.float32), 0.0, None) * 100.0
    saddle = saddle_score_grid(dem, cell_m)
    pinch = pinch_score_grid(
        slope_pct, cell_m,
        steep_slope_pct=float(settings.get("scout_steep_slope_pct", 20.0)),
        max_pinch_width_m=float(settings.get("scout_max_pinch_width_m", 120.0)),
    )
    return {"saddle": saddle, "pinch": pinch, "combined": np.maximum(saddle, pinch)}
