"""Pydantic validation schemas for regions."""
from __future__ import annotations

from pydantic import BaseModel, Field

# Comfortably under the `regions.name` column's VARCHAR(120) — rejecting an
# over-length name here gives a clean 422 instead of a raw DB error, and
# matches the frontend input's maxLength so both layers agree.
REGION_NAME_MAX_LEN = 60


class RegionIn(BaseModel):
    name: str = Field(min_length=1, max_length=REGION_NAME_MAX_LEN)
    lat: float
    lon: float
    rut_peak_month: int = 12
    rut_peak_day: int = 5
    property_timezone: str = "America/Chicago"
