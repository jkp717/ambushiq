"""Pydantic validation schemas for stands."""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel


class StandIn(BaseModel):
    name: str
    lat: float
    lon: float
    is_active: bool = True
    downhill_deg: Optional[int] = None
    deer_approach_deg: Optional[int] = None
    visibility_m: Optional[float] = None  # None -> use corridor/global falloff
