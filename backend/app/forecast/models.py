"""Database ORM model for the persisted weather cache."""
from __future__ import annotations

from sqlalchemy import Float, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class ForecastCache(Base):
    """One cached upstream weather response (forecast or historical archive), stored as JSON so
    the in-memory cache survives container restarts."""
    __tablename__ = "forecast_cache"
    key: Mapped[str] = mapped_column(String(200), primary_key=True)
    fetched_at: Mapped[float] = mapped_column(Float)   # epoch seconds
    payload: Mapped[str] = mapped_column(Text)
