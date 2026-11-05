"""Joining line segments into whole roads/trails for the map layers (app.roads, app.trails).

Both sources (USGS National Map, USFS MVUM) store one road or trail as many segments, split at intersections and
wherever the source data changes, so drawing one feature per segment made the map's hover highlight a
random-length piece. Segments are merged per request, after the cells covering the view are combined: a road
crosses several cells, so merging per cell would draw overlapping duplicates. Cells keep caching raw segments."""
from __future__ import annotations

import asyncio
from typing import Callable, Hashable


def _line_parts(geom: dict) -> list:
    return [geom["coordinates"]] if geom["type"] == "LineString" else geom["coordinates"]


def _endpoints(geom: dict) -> set:
    return {tuple(p) for part in _line_parts(geom) for p in (part[0], part[-1])}


def merge_line_segments(features: list[dict], group_key: Callable[[dict], Hashable],
                        is_unnamed: Callable[[dict], bool]) -> list[dict]:
    """Segments with the same `group_key(properties)` that touch end-to-end become one (Multi)LineString feature.
    Same-key lines that don't connect stay separate (two different "Main St"s). Unnamed segments join only where
    exactly two segments of any line meet (a bend or a data split), so an intersection ends them; otherwise a whole
    unnamed network would light up as one. Coordinates are already rounded to 5 decimals, so shared endpoints match
    exactly. A merged feature keeps the smallest member id (as a string), so a lone segment's id is unchanged and ids
    don't depend on the order the source returned them."""
    n = len(features)
    parent = list(range(n))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    touching: dict[tuple, list[int]] = {}       # endpoint -> every segment ending there
    for i, f in enumerate(features):
        for p in _endpoints(f["geometry"]):
            touching.setdefault(p, []).append(i)

    for idxs in touching.values():
        if len(idxs) < 2:
            continue
        by_key: dict[Hashable, list[int]] = {}
        for i in idxs:
            by_key.setdefault(group_key(features[i]["properties"]), []).append(i)
        for same in by_key.values():
            if len(same) < 2 or (is_unnamed(features[same[0]]["properties"]) and len(idxs) != 2):
                continue
            root = find(same[0])
            for i in same[1:]:
                parent[find(i)] = root

    groups: dict[int, list[int]] = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(i)
    out = []
    for members in groups.values():
        if len(members) == 1:
            out.append(features[members[0]])
            continue
        first = min(members, key=lambda i: str(features[i]["id"]))
        parts = [part for i in members for part in _line_parts(features[i]["geometry"])]
        out.append({**features[first], "geometry": {"type": "MultiLineString", "coordinates": parts}})
    return out


class MergeMemo:
    """Remembers the last merge per set of cells: panning within the same cells (the common case) reuses it instead
    of redoing it (~100 ms at the zoom-11 worst case). Each entry holds the exact cell feature lists it was built
    from, so a refreshed or failed cell (a different list object) is a miss."""

    def __init__(self, size: int = 8):   # small: each entry holds a whole view's lines, and memory is tight
        self.size = size
        self._entries: dict[Hashable, tuple[list, list]] = {}

    def clear(self) -> None:
        self._entries.clear()

    async def get(self, key: Hashable, sources: list[list], features: list[dict],
                  merge: Callable[[list[dict]], list[dict]]) -> list[dict]:
        hit = self._entries.get(key)
        if hit and len(hit[0]) == len(sources) and all(a is b for a, b in zip(hit[0], sources)):
            return hit[1]
        out = await asyncio.to_thread(merge, features)
        if key not in self._entries and len(self._entries) >= self.size:
            self._entries.pop(next(iter(self._entries)))
        self._entries[key] = (sources, out)
        return out
