"""Pydantic validation schemas for scouting suggestions.

Radius bounds are enforced against live settings (scout_radius_min_m/max_m) inside
the router, not as static Field(ge=, le=) constraints here, since those bounds are
themselves user-adjustable in Settings.
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator


class ScoutingAnalyzeIn(BaseModel):
    lat: float
    lon: float
    radius_m: float
    mode: Optional[Literal["override", "merge"]] = None


class ScoutingStatusIn(BaseModel):
    status: Literal["new", "dismissed"]


class ScoutingColorIn(BaseModel):
    # "#RRGGBB", or null to go back to the default color
    color: Optional[str] = Field(default=None, pattern=r"^#[0-9A-Fa-f]{6}$")


class ScoutingCommentIn(BaseModel):
    text: str = Field(max_length=2000)

    @field_validator("text")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("comment can't be empty")
        return v
