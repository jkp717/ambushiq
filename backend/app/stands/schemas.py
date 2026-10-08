"""Pydantic validation schemas for stands."""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field


class StandIn(BaseModel):
    name: str
    lat: float
    lon: float
    is_active: bool = True
    downhill_deg: Optional[int] = None
    deer_approach_deg: Optional[int] = None
    visibility_m: Optional[float] = None  # None -> use corridor/global falloff
    # map pin only; on update, left unchanged when omitted
    stand_type: Literal["tree", "blind", "spot"] = "tree"
    color: Optional[str] = Field(default=None, pattern=r"^#[0-9A-Fa-f]{6}$")  # None -> default red
