"""Pydantic validation schemas for zones."""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel


class ZoneIn(BaseModel):
    kind: str
    name: Optional[str] = None
    lat: float
    lon: float
    radius_m: int = 80
    is_active: bool = True
    quality: Optional[int] = None  # food zones only; 1=poor … 10=premium

