"""Shared dependency: resolves the active region from the X-Region-Id header."""
from __future__ import annotations

from fastapi import Header, HTTPException
from sqlalchemy.orm import Session

from app.core.database import engine
from app.regions.models import Region


def get_active_region_id(x_region_id: str | None = Header(default=None, alias="X-Region-Id")) -> int:
    if not x_region_id:
        raise HTTPException(400, "missing X-Region-Id header")
    try:
        region_id = int(x_region_id)
    except (TypeError, ValueError):
        raise HTTPException(400, "invalid X-Region-Id header")
    with Session(engine) as s:
        if not s.get(Region, region_id):
            raise HTTPException(400, f"unknown region_id {region_id}")
    return region_id
