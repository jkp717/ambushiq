"""Pydantic validation schemas for scouting suggestions.

Radius bounds are enforced against live settings (scout_radius_min_m/max_m) inside
the router, not as static Field(ge=, le=) constraints here, since those bounds are
themselves user-adjustable in Settings.
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel


class ScoutingAnalyzeIn(BaseModel):
    lat: float
    lon: float
    radius_m: float
    mode: Optional[Literal["override", "merge"]] = None


class ScoutingStatusIn(BaseModel):
    status: Literal["new", "dismissed"]
