"""Route handlers for /api/sign (deer sign: scrapes + rubs)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import engine
from app.dependencies import get_active_region_id, require_token
from app.deer_sign.models import DeerSign
from app.deer_sign.schemas import DeerSignIn

router = APIRouter(prefix="/api/sign", tags=["deer_sign"])


@router.get("")
def list_sign(region_id: int = Depends(get_active_region_id), _=Depends(require_token)):
    with Session(engine) as s:
        return [r.to_dict() for r in s.scalars(select(DeerSign).where(DeerSign.region_id == region_id)).all()]

@router.post("")
def create_sign(body: DeerSignIn, region_id: int = Depends(get_active_region_id), _=Depends(require_token)):
    from datetime import datetime
    now = datetime.now()
    name = f"{body.kind.title()} {now.month}/{now.day}"
    row = DeerSign(kind=body.kind, name=name, lat=body.lat, lon=body.lon,
                   is_active=int(body.is_active), region_id=region_id,
                   created_at=now.isoformat(timespec="seconds"))
    with Session(engine) as s:
        s.add(row); s.commit(); s.refresh(row)
        return row.to_dict()

@router.put("/{sign_id}")
def update_sign(sign_id: int, body: DeerSignIn, region_id: int = Depends(get_active_region_id),
                 _=Depends(require_token)):
    with Session(engine) as s:
        row = s.get(DeerSign, sign_id)
        if not row or row.region_id != region_id:
            raise HTTPException(404, "not found")
        row.is_active = int(body.is_active)
        s.commit(); s.refresh(row)
        return row.to_dict()

@router.delete("/{sign_id}")
def delete_sign(sign_id: int, region_id: int = Depends(get_active_region_id), _=Depends(require_token)):
    with Session(engine) as s:
        row = s.get(DeerSign, sign_id)
        if row and row.region_id == region_id:
            s.delete(row); s.commit()
    return {"ok": True}
