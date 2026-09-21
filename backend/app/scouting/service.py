"""Orchestrates one full Scouting Suggestions analysis run: shared elevation +
land-cover fetch, terrain-funnel/habitat-edge detection, scoring, clustering,
and persistence — composed as a single 0-100 progress stream.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import httpx
import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.cameras.models import CameraSighting
from app.corridors.models import Corridor
from app.core.database import engine
from app.deer_sign.models import DeerSign
from app.forecast.service import _haversine_m
from app.scouting import landcover, scoring, terrain_features
from app.scouting.models import ScoutingSuggestion
from app.stands.models import Stand
from app.stands.terrain import _fetch_open_meteo, _fetch_usgs, build_sample_grid
from app.zones.models import Zone


async def analyze_area(lat: float, lon: float, radius_m: float, mode: str | None,
                        settings: dict, region_id: int, progress_callback=None) -> list[dict]:
    async def cb(pct, msg):
        if progress_callback:
            await progress_callback(pct, msg)

    grid_n = int(settings.get("scout_grid_n", 60))
    box_m = 2 * radius_m
    lats, lons, cell_m = build_sample_grid(lat, lon, grid=grid_n, box_m=box_m)
    await cb(2, "Initializing analysis grid...")

    async with httpx.AsyncClient() as client:
        try:
            flat = await _fetch_usgs(client, lats, lons, progress_callback, progress_span=(2, 42))
        except Exception:
            await cb(30, "USGS limit reached: switching to Open-Meteo...")
            flat = await _fetch_open_meteo(client, lats, lons)

        await cb(44, "Fetching land cover...")
        nlcd_flat = await landcover.fetch_landcover(client, lats, lons, progress_callback, progress_span=(44, 80))

    await cb(82, "Detecting terrain funnels & habitat edges...")
    dem_list = [flat[r * grid_n:(r + 1) * grid_n] for r in range(grid_n)]
    dem = np.array(dem_list, dtype=np.float32)
    funnel = terrain_features.funnel_score_grid(dem, cell_m, settings)
    edge = landcover.edge_score_grid(nlcd_flat, grid_n)

    await cb(88, "Loading your zones, corridors, and sign...")
    with Session(engine) as s:
        zones = [z.to_dict() for z in s.scalars(
            select(Zone).where(Zone.is_active == 1, Zone.region_id == region_id)).all()]
        corridors = [c.to_dict() for c in s.scalars(
            select(Corridor).where(Corridor.is_active == 1, Corridor.region_id == region_id)).all()]
        sign = [r.to_dict() for r in s.scalars(
            select(DeerSign).where(DeerSign.is_active == 1, DeerSign.region_id == region_id)).all()]
        stands = [r.to_dict() for r in s.scalars(
            select(Stand).where(Stand.is_active == 1, Stand.region_id == region_id)).all()]
        stand_by_id = {st["id"]: st for st in stands}

        # CameraSighting has no region_id of its own — filtered transitively
        # since it only keeps sightings whose stand_id is in the already
        # region-filtered stand_by_id map above.
        camera_sightings = []
        for row in s.scalars(select(CameraSighting).where(CameraSighting.is_animal == 1)).all():
            st = stand_by_id.get(row.stand_id)
            if not st:
                continue
            camera_sightings.append({
                "lat": st["lat"], "lon": st["lon"],
                "timestamp": row.timestamp, "confidence_score": row.confidence_score,
                "species": row.species,
            })

        existing = [r.to_dict() for r in s.scalars(
            select(ScoutingSuggestion).where(ScoutingSuggestion.region_id == region_id)).all()]

    known_points = [(st["lat"], st["lon"]) for st in stands] + [(e["lat"], e["lon"]) for e in existing]

    await cb(92, "Scoring candidates...")
    candidates = scoring.score_grid(lats, lons, funnel["combined"], edge, zones, corridors, sign,
                                     settings, camera_sightings, known_points)

    if mode == "merge":
        existing_in_circle = [
            e for e in existing
            if _haversine_m(lat, lon, e["lat"], e["lon"]) <= radius_m + e.get("radius_m", 0)
        ]
        if existing_in_circle:
            candidates = scoring.filter_against_existing(candidates, existing_in_circle, settings)

    clustered = scoring.cluster_candidates(candidates, settings)

    await cb(97, "Saving suggestions...")
    suggestion_radius = float(settings.get("scout_suggestion_radius_m", 60.0))
    now_iso = datetime.now(timezone.utc).isoformat()
    with Session(engine) as s:
        if mode == "override":
            for row in s.scalars(
                select(ScoutingSuggestion).where(ScoutingSuggestion.region_id == region_id)
            ).all():
                if _haversine_m(lat, lon, row.lat, row.lon) <= radius_m:
                    s.delete(row)
            s.commit()

        saved_rows = []
        for cand in clustered:
            row = ScoutingSuggestion(
                lat=cand["lat"], lon=cand["lon"], radius_m=suggestion_radius,
                score=round(cand["score"], 1),
                reasoning_json=json.dumps({
                    "breakdown": cand["breakdown"],
                    "text": scoring.render_reasoning(cand["breakdown"]),
                }),
                status="new", region_id=region_id,
                request_lat=lat, request_lon=lon, request_radius_m=radius_m,
                created_at=now_iso,
            )
            s.add(row)
            saved_rows.append(row)
        s.commit()
        for row in saved_rows:
            s.refresh(row)
        result = [row.to_dict() for row in saved_rows]

    await cb(100, "Complete!")
    return result
