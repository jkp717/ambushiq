"""Database ORM model for the general road network cache."""
from __future__ import annotations

from sqlalchemy import Float, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class RoadCell(Base):
    """One map grid cell's worth of already-simplified road lines, stored as GeoJSON features.
    Road networks change rarely, so a cell is fetched from USGS once and reused for weeks."""
    __tablename__ = "road_cells"
    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    fetched_at: Mapped[float] = mapped_column(Float)   # epoch seconds
    payload: Mapped[str] = mapped_column(Text)
