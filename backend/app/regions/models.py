"""Database ORM model for hunting regions."""
from __future__ import annotations

from sqlalchemy import Float, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class Region(Base):
    __tablename__ = "regions"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(120))
    lat: Mapped[float] = mapped_column(Float)
    lon: Mapped[float] = mapped_column(Float)
    rut_peak_month: Mapped[int] = mapped_column(Integer, default=12, server_default="12")
    rut_peak_day: Mapped[int] = mapped_column(Integer, default=5, server_default="5")
    property_timezone: Mapped[str] = mapped_column(String(64), default="America/Chicago",
                                                     server_default="America/Chicago")
    is_default: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    created_at: Mapped[str] = mapped_column(String(32), default="")

    def to_dict(self) -> dict:
        return {
            "id": self.id, "name": self.name, "lat": self.lat, "lon": self.lon,
            "rut_peak_month": self.rut_peak_month, "rut_peak_day": self.rut_peak_day,
            "property_timezone": self.property_timezone,
            "is_default": bool(self.is_default), "created_at": self.created_at,
        }


class RegionTerrain(Base):
    """One property-wide elevation grid per region (regions/terrain.py), used for lee-eddy
    detection. north/south/west/east are the outermost sample points; row 0 is north."""
    __tablename__ = "region_terrain"
    region_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    north: Mapped[float] = mapped_column(Float)
    south: Mapped[float] = mapped_column(Float)
    west: Mapped[float] = mapped_column(Float)
    east: Mapped[float] = mapped_column(Float)
    rows: Mapped[int] = mapped_column(Integer)
    cols: Mapped[int] = mapped_column(Integer)
    cell_m: Mapped[float] = mapped_column(Float)
    dem_json: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(32))
    fetched_at: Mapped[str] = mapped_column(String(40))
