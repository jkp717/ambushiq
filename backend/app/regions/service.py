"""Business logic for resolving Region rows."""
from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.core.database import engine
from app.regions.models import Region


def get_region_dict(region_id: int) -> dict:
    """Load one region as a dict. Callers reach here only via a route that
    already depended on get_active_region_id (which 400s on an unknown id),
    so the 400 here is defensive, not expected in practice."""
    with Session(engine) as s:
        region = s.get(Region, region_id)
        if not region:
            raise HTTPException(400, "unknown region")
        return region.to_dict()
