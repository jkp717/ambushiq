"""Database ORM model for the off-road trail/road boundary cache."""
from __future__ import annotations

from sqlalchemy import Float, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class TrailCell(Base):
    """One map grid cell's worth of already-simplified trail/road lines, stored as GeoJSON features.
    Designated-use lines change rarely, so a cell is fetched from USFS once and reused for weeks."""
    __tablename__ = "trail_cells"
    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    fetched_at: Mapped[float] = mapped_column(Float)   # epoch seconds
    payload: Mapped[str] = mapped_column(Text)
