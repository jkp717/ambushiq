"""Route handlers for /api/corridors."""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import engine
from app.dependencies import require_token
from app.corridors.models import Corridor
from app.corridors.schemas import CorridorIn

router = APIRouter(prefix="/api/corridors", tags=["corridors"])


@router.get("")
def list_corridors(_=Depends(require_token)):
    with Session(engine) as s:
        return [c.to_dict() for c in s.scalars(select(Corridor)).all()]


@router.post("")
def create_corridor(body: CorridorIn, _=Depends(require_token)):
    if len(body.points) < 2:
        raise HTTPException(400, "a corridor needs at least 2 points")
    with Session(engine) as s:
        c = Corridor(name=body.name, points_json=json.dumps(body.points),
                     is_active=1 if body.is_active else 0,
                     usage=max(1, min(10, body.usage)), falloff_m=body.falloff_m,
                     width_m=body.width_m)
        s.add(c)
        s.commit()
        s.refresh(c)
        return c.to_dict()


@router.put("/{corridor_id}")
def update_corridor(corridor_id: int, body: CorridorIn, _=Depends(require_token)):
    with Session(engine) as s:
        c = s.get(Corridor, corridor_id)
        if not c:
            raise HTTPException(404, "not found")
        c.name = body.name
        c.is_active = 1 if body.is_active else 0
        c.usage = max(1, min(10, body.usage))
        c.falloff_m = body.falloff_m
        c.width_m = body.width_m
        if body.points and len(body.points) >= 2:
            c.points_json = json.dumps(body.points)
        s.commit()
        s.refresh(c)
        return c.to_dict()


@router.delete("/{corridor_id}")
def delete_corridor(corridor_id: int, _=Depends(require_token)):
    with Session(engine) as s:
        c = s.get(Corridor, corridor_id)
        if c:
            s.delete(c)
            s.commit()
    return {"ok": True}
