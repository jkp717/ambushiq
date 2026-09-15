"""Pydantic validation schemas for corridors."""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel


class CorridorIn(BaseModel):
    name: Optional[str] = None
    points: list[list[float]]
    is_active: bool = True
    usage: int = 5          # 1 = rarely used, 10 = heavily used
    falloff_m: Optional[float] = None  # None → inherit global falloff_corridor
    width_m: Optional[float] = None    # None/0 -> treated as a thin travel line

