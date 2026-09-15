"""Pydantic validation schemas for forecast/ranking endpoints."""
from __future__ import annotations

from pydantic import BaseModel


class SitRankIn(BaseModel):
    sit_idxs: list[int]
    sunrise_h: float
    sunset_h: float


class ManualRankIn(BaseModel):
    wind_dir: str
    wind_speed: float
    gust: float
    period: str  # morning|midday|evening


class HourRankIn(BaseModel):
    time_index: int  # index into the forecast hourly arrays


class DayRankIn(BaseModel):
    day: str  # "YYYY-MM-DD"
    use_corridor: bool = True
    use_food: bool = True
    use_bedding: bool = True
