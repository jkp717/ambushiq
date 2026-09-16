"""Route handlers for /api/regions."""
from __future__ import annotations

import logging
import os
import shutil
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import delete as sa_delete, func, select, update as sa_update
from sqlalchemy.orm import Session

from app.cameras.models import Camera, CameraSighting
from app.core.config import CAMERA_IMAGE_DIR
from app.core.database import engine
from app.corridors.models import Corridor
from app.deer_sign.models import DeerSign
from app.dependencies import require_token
from app.regions.models import Region
from app.regions.schemas import RegionIn
from app.scouting.models import ScoutingSuggestion
from app.settings.service import get_settings
from app.stands.models import Stand
from app.zones.models import Zone

router = APIRouter(prefix="/api/regions", tags=["regions"])

log = logging.getLogger(__name__)


@router.get("")
def list_regions(_=Depends(require_token)):
    with Session(engine) as s:
        return [r.to_dict() for r in s.scalars(select(Region).order_by(Region.name)).all()]


@router.post("")
def create_region(body: RegionIn, _=Depends(require_token)):
    with Session(engine) as s:
        existing_count = s.execute(select(func.count(Region.id))).scalar_one()
        region = Region(
            name=body.name, lat=body.lat, lon=body.lon,
            rut_peak_month=body.rut_peak_month, rut_peak_day=body.rut_peak_day,
            property_timezone=body.property_timezone,
            is_default=1 if existing_count == 0 else 0,
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        s.add(region)
        s.commit()
        s.refresh(region)
        return region.to_dict()


@router.put("/{region_id}")
def update_region(region_id: int, body: RegionIn, _=Depends(require_token)):
    with Session(engine) as s:
        region = s.get(Region, region_id)
        if not region:
            raise HTTPException(404, "not found")
        region.name = body.name
        region.lat = body.lat
        region.lon = body.lon
        region.rut_peak_month = body.rut_peak_month
        region.rut_peak_day = body.rut_peak_day
        region.property_timezone = body.property_timezone
        s.commit()
        s.refresh(region)
        return region.to_dict()


@router.post("/{region_id}/default")
def set_default_region(region_id: int, _=Depends(require_token)):
    with Session(engine) as s:
        region = s.get(Region, region_id)
        if not region:
            raise HTTPException(404, "not found")
        s.execute(sa_update(Region).values(is_default=0))
        region.is_default = 1
        s.commit()
        s.refresh(region)
        return region.to_dict()


@router.delete("/{region_id}")
def delete_region(region_id: int, _=Depends(require_token)):
    with Session(engine) as s:
        region = s.get(Region, region_id)
        if not region:
            raise HTTPException(404, "not found")
        total = s.execute(select(func.count(Region.id))).scalar_one()
        if total <= 1:
            raise HTTPException(400, "at least one region must remain")

        # CameraSighting has no region_id of its own — cascade transitively via
        # the informal stand_id/camera_id "FKs" (belt-and-suspenders: cover
        # both, even though every sighting today is always written with both set).
        stand_ids = select(Stand.id).where(Stand.region_id == region_id)
        camera_ids = select(Camera.id).where(Camera.region_id == region_id)
        s.execute(sa_delete(CameraSighting).where(
            CameraSighting.camera_id.in_(camera_ids) | CameraSighting.stand_id.in_(stand_ids)
        ))
        for model in (ScoutingSuggestion, DeerSign, Corridor, Zone, Camera, Stand):
            s.execute(sa_delete(model).where(model.region_id == region_id))

        was_default = bool(region.is_default)
        s.delete(region)
        s.commit()

        if was_default:
            fallback = s.scalars(select(Region).order_by(Region.id)).first()
            if fallback:
                fallback.is_default = 1
                s.commit()

    base_dir = str(get_settings().get("camera_image_dir") or CAMERA_IMAGE_DIR)
    image_dir = os.path.join(base_dir, f"region_{region_id}")
    if os.path.isdir(image_dir):
        try:
            shutil.rmtree(image_dir)
        except Exception as exc:
            log.warning("delete_region: failed to remove %s: %s", image_dir, exc)
    return {"ok": True}
