"""Database ORM model for the persisted weather cache."""
from __future__ import annotations

from typing import Optional

from sqlalchemy import Float, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class ForecastCache(Base):
    """One cached upstream weather response (forecast or historical archive), stored as JSON so
    the in-memory cache survives container restarts."""
    __tablename__ = "forecast_cache"
    key: Mapped[str] = mapped_column(String(200), primary_key=True)
    fetched_at: Mapped[float] = mapped_column(Float)   # epoch seconds
    payload: Mapped[str] = mapped_column(Text)


class WeatherHour(Base):
    """One hour of observed weather at a region's property, kept for good (past weather doesn't change) so
    camera activity can be charted against it. From Open-Meteo's archive; the last few days, which the
    archive doesn't have yet, come from its forecast model and are `provisional` until the archive does."""
    __tablename__ = "weather_hours"
    region_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    hour_utc: Mapped[str] = mapped_column(String(20), primary_key=True)   # "2025-11-08T12:00Z"
    temp_f: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    pressure_inhg: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    wind_mph: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    precip_in: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    provisional: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
