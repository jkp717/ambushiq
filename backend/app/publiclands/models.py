"""Database ORM model for the public-land boundary cache."""
from __future__ import annotations

from sqlalchemy import Float, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class PublicLandCell(Base):
    """One map grid cell's worth of already-simplified public land boundaries, stored as GeoJSON features.
    Boundaries change rarely, so a cell is fetched from USGS once and reused for weeks."""
    __tablename__ = "public_land_cells"
    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    fetched_at: Mapped[float] = mapped_column(Float)   # epoch seconds
    payload: Mapped[str] = mapped_column(Text)
