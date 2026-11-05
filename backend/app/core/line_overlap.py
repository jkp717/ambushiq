"""Where two line datasets draw the same road: USGS roads (app.roads) vs Forest Service MVUM roads (app.trails).

Both sources carry most national-forest roads, digitized separately, so the map showed two near-parallel lines a few
metres apart with different names (e.g. USGS "10" and FS road 494 "GLEN STREET"). `overlap` finds, for each line,
the stretches that run along a "cover" line: within `max_m` of it and roughly parallel, so a road that merely crosses
another isn't cut at the crossing. The roads layer uses it to cut USGS roads out under FS roads; the trails layer uses
it to list the USGS names an FS road covers.

Lines are walked in ~STEP_M steps in a local flat projection (metres), against a grid index of cover segments; a line
with no point near any indexed cell is skipped untouched, so roads outside national forests cost almost nothing."""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional

M_PER_DEG = 111_320.0
STEP_M = 10.0
CELL_M = 100.0
MIN_RUN_M = 40.0   # uncovered gaps shorter than this between covered stretches count as covered; uncovered ends
                   # this short beside a covered stretch are dropped as slivers


@dataclass
class LineOverlap:
    """For one input line: `pieces` is None when nothing is covered (keep the feature as it is), else the uncovered
    parts as [lon, lat] coordinate lists (possibly none). `covered_m` is covered length per cover feature id."""
    pieces: Optional[list] = None
    covered_m: dict = field(default_factory=dict)


def _parts(geom: dict) -> list:
    return [geom["coordinates"]] if geom["type"] == "LineString" else geom["coordinates"]


class _Proj:
    def __init__(self, lat0: float):
        self.kx = M_PER_DEG * math.cos(math.radians(lat0))

    def fwd(self, p) -> tuple[float, float]:
        return p[0] * self.kx, p[1] * M_PER_DEG

    def back(self, x: float, y: float) -> list:
        return [round(x / self.kx, 6), round(y / M_PER_DEG, 6)]


def _cell(x: float, y: float) -> tuple[int, int]:
    return math.floor(x / CELL_M), math.floor(y / CELL_M)


def _line_cells(parts_m: list) -> set:
    """Every index cell the lines pass through (sampled every cell width), so only cover segments there are indexed."""
    cells = set()
    for pts in parts_m:
        for (ax, ay), (bx, by) in zip(pts, pts[1:]):
            n = max(1, int(math.hypot(bx - ax, by - ay) // CELL_M) + 1)
            for i in range(n + 1):
                t = i / n
                cells.add(_cell(ax + t * (bx - ax), ay + t * (by - ay)))
        if pts:
            cells.add(_cell(*pts[0]))
    # neighbours too: sampling once per cell width can step over a corner the line clips
    return {(cx + dx, cy + dy) for cx, cy in cells for dx in (-1, 0, 1) for dy in (-1, 0, 1)}


def _index(cover_feats: list, proj: _Proj, max_m: float, wanted: Optional[set] = None) -> dict:
    """Grid cell -> cover segments (ax, ay, bx, by, cover id) whose box, grown by max_m, touches the cell. With
    `wanted`, only those cells are filled (the cells the lines being checked pass through)."""
    grid: dict = {}
    for f in cover_feats:
        for part in _parts(f["geometry"]):
            pts = [proj.fwd(p) for p in part]
            for (ax, ay), (bx, by) in zip(pts, pts[1:]):
                seg = (ax, ay, bx, by, f["id"])
                x0, y0 = _cell(min(ax, bx) - max_m, min(ay, by) - max_m)
                x1, y1 = _cell(max(ax, bx) + max_m, max(ay, by) + max_m)
                for cx in range(x0, x1 + 1):
                    for cy in range(y0, y1 + 1):
                        if wanted is None or (cx, cy) in wanted:
                            grid.setdefault((cx, cy), []).append(seg)
    return grid


def _near(grid: dict, pts: list) -> bool:
    """Any index cell along the line (sampled every cell width)? Cheap pre-check before the fine walk."""
    for (ax, ay), (bx, by) in zip(pts, pts[1:]):
        n = max(1, int(math.hypot(bx - ax, by - ay) // CELL_M) + 1)
        for i in range(n + 1):
            t = i / n
            if _cell(ax + t * (bx - ax), ay + t * (by - ay)) in grid:
                return True
    return bool(pts) and _cell(*pts[0]) in grid


def _walk(pts: list) -> list:
    """Points every ~STEP_M along the line: (x, y, is_original_vertex, unit direction dx, dy, step length)."""
    out = []
    for (ax, ay), (bx, by) in zip(pts, pts[1:]):
        d = math.hypot(bx - ax, by - ay)
        if d == 0:
            continue
        ux, uy = (bx - ax) / d, (by - ay) / d
        n = max(1, math.ceil(d / STEP_M))
        for i in range(n):
            t = i / n
            out.append((ax + t * (bx - ax), ay + t * (by - ay), i == 0, ux, uy, d / n))
    if out:
        bx, by = pts[-1]
        out.append((bx, by, True, out[-1][3], out[-1][4], 0.0))
    return out


def _covering(grid: dict, x: float, y: float, ux: float, uy: float, max_m: float, min_cos: float):
    """Id of the nearest roughly-parallel cover segment within max_m of the point, else None."""
    best, best_d = None, max_m
    for ax, ay, bx, by, cid in grid.get(_cell(x, y), ()):
        dx, dy = bx - ax, by - ay
        seg2 = dx * dx + dy * dy
        if seg2 == 0:
            continue
        if abs(ux * dx + uy * dy) / math.sqrt(seg2) < min_cos:
            continue
        t = max(0.0, min(1.0, ((x - ax) * dx + (y - ay) * dy) / seg2))
        d = math.hypot(x - (ax + t * dx), y - (ay + t * dy))
        if d <= best_d:
            best, best_d = cid, d
    return best


def _smooth(flags: list, steps: list) -> list:
    """Short uncovered gaps between covered stretches become covered; short uncovered ends beside one too."""
    n = len(flags)
    out = list(flags)
    i = 0
    while i < n:
        if flags[i]:
            i += 1
            continue
        j = i
        while j < n and not flags[j]:
            j += 1
        length = sum(steps[i:j])
        touches = (i > 0) or (j < n)
        if touches and length < MIN_RUN_M:
            for k in range(i, j):
                out[k] = True
        i = j
    return out


def overlap(line_feats: list, cover_feats: list, max_m: float = 25.0, max_angle: float = 30.0) -> list[LineOverlap]:
    """One LineOverlap per line feature, in order (see the module docstring)."""
    results = [LineOverlap() for _ in line_feats]
    if not line_feats or not cover_feats:
        return results
    first = _parts(cover_feats[0]["geometry"])[0][0]
    proj = _Proj(first[1])
    line_parts = [[[proj.fwd(p) for p in part] for part in _parts(f["geometry"])] for f in line_feats]
    grid = _index(cover_feats, proj, max_m, _line_cells([pts for parts in line_parts for pts in parts]))
    min_cos = math.cos(math.radians(max_angle))

    for res, f, parts in zip(results, line_feats, line_parts):
        if not any(_near(grid, pts) for pts in parts):
            continue
        pieces, any_covered = [], False
        for pts in parts:
            walk = _walk(pts)
            if not walk:
                continue
            ids = [_covering(grid, x, y, ux, uy, max_m, min_cos) for x, y, _o, ux, uy, _s in walk]
            for cid, w in zip(ids, walk):
                if cid is not None:
                    res.covered_m[cid] = res.covered_m.get(cid, 0.0) + w[5]
            flags = _smooth([cid is not None for cid in ids], [w[5] for w in walk])
            if not any(flags):
                pieces.append([proj.back(x, y) for x, y in pts])
                continue
            any_covered = True
            run: list = []
            for k, (x, y, orig, *_rest) in enumerate(walk):
                if flags[k]:
                    if len(run) >= 2:
                        pieces.append(run)
                    run = []
                    continue
                at_run_edge = not run or k == len(walk) - 1 or flags[k + 1]
                if orig or at_run_edge:
                    run.append(proj.back(x, y))
            if len(run) >= 2:
                pieces.append(run)
        if any_covered:
            res.pieces = pieces
    return results


def cut_covered(features: list[dict], cover_feats: list[dict], **kw) -> list[dict]:
    """`features` with every stretch that runs along a cover line removed. A feature with nothing covered is
    returned as is; a covered one becomes its uncovered pieces, with ids "{id}~{n}" (possibly none)."""
    if not features or not cover_feats:
        return features
    out = []
    for f, res in zip(features, overlap(features, cover_feats, **kw)):
        if res.pieces is None:
            out.append(f)
            continue
        for n, piece in enumerate(res.pieces):
            out.append({**f, "id": f"{f['id']}~{n}", "geometry": {"type": "LineString", "coordinates": piece}})
    return out
