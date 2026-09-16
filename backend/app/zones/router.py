"""Route handlers for /api/zones."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import engine
from app.dependencies import get_active_region_id, require_token
from app.zones.models import Zone
from app.zones.schemas import ZoneIn

router = APIRouter(prefix="/api/zones", tags=["zones"])


@router.get("")
def list_zones(region_id: int = Depends(get_active_region_id), _=Depends(require_token)):
    with Session(engine) as s:
        return [z.to_dict() for z in s.scalars(select(Zone).where(Zone.region_id == region_id)).all()]


@router.post("")
def create_zone(body: ZoneIn, region_id: int = Depends(get_active_region_id), _=Depends(require_token)):
    with Session(engine) as s:
        z = Zone(**body.model_dump(), region_id=region_id)
        s.add(z)
        s.commit()
        s.refresh(z)
        return z.to_dict()


@router.put("/{zone_id}")
def update_zone(zone_id: int, body: ZoneIn, region_id: int = Depends(get_active_region_id),
                 _=Depends(require_token)):
    with Session(engine) as s:
        z = s.get(Zone, zone_id)
        if not z or z.region_id != region_id:
            raise HTTPException(404, "not found")
        for k, v in body.model_dump().items():
            setattr(z, k, 1 if (k == "is_active" and v) else (0 if k == "is_active" else v))
        s.commit()
        s.refresh(z)
        return z.to_dict()


@router.delete("/{zone_id}")
def delete_zone(zone_id: int, region_id: int = Depends(get_active_region_id), _=Depends(require_token)):
    with Session(engine) as s:
        z = s.get(Zone, zone_id)
        if z and z.region_id == region_id:
            s.delete(z)
            s.commit()
    return {"ok": True}
