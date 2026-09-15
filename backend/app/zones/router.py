"""Route handlers for /api/zones."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import engine
from app.dependencies import require_token
from app.zones.models import Zone
from app.zones.schemas import ZoneIn

router = APIRouter(prefix="/api/zones", tags=["zones"])


@router.get("")
def list_zones(_=Depends(require_token)):
    with Session(engine) as s:
        return [z.to_dict() for z in s.scalars(select(Zone)).all()]


@router.post("")
def create_zone(body: ZoneIn, _=Depends(require_token)):
    with Session(engine) as s:
        z = Zone(**body.model_dump())
        s.add(z)
        s.commit()
        s.refresh(z)
        return z.to_dict()


@router.put("/{zone_id}")
def update_zone(zone_id: int, body: ZoneIn, _=Depends(require_token)):
    with Session(engine) as s:
        z = s.get(Zone, zone_id)
        if not z:
            raise HTTPException(404, "not found")
        for k, v in body.model_dump().items():
            setattr(z, k, 1 if (k == "is_active" and v) else (0 if k == "is_active" else v))
        s.commit()
        s.refresh(z)
        return z.to_dict()


@router.delete("/{zone_id}")
def delete_zone(zone_id: int, _=Depends(require_token)):
    with Session(engine) as s:
        z = s.get(Zone, zone_id)
        if z:
            s.delete(z)
            s.commit()
    return {"ok": True}
