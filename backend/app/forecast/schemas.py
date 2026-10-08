"""Pydantic validation schemas for forecast/ranking endpoints."""
from __future__ import annotations

from typing import Literal

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
    minute: Literal[0, 15, 30, 45] = 0  # 15-minute slot within the hour (inputs eased toward the next hour)
    lee_zone: bool = False  # include the property-wide lee-eddy map layer for this hour


class DayRankIn(BaseModel):
    day: str  # "YYYY-MM-DD"
    use_corridor: bool = True
    use_food: bool = True
    use_bedding: bool = True
