"""Database ORM models for stands."""
from __future__ import annotations

import json
from typing import Optional

from sqlalchemy import Float, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class Stand(Base):
    __tablename__ = "stands"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    region_id: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str] = mapped_column(String(120))
    lat: Mapped[float] = mapped_column(Float)
    lon: Mapped[float] = mapped_column(Float)
    is_active: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    downhill_deg: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    deer_approach_deg: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    terrain_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # effective sight/cover radius (m); None -> falls back to corridor/global falloff
    visibility_m: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    def to_dict(self) -> dict:
        return {
            "id": self.id, "name": self.name, "lat": self.lat, "lon": self.lon,
            "is_active": bool(self.is_active if self.is_active is not None else 1),
            "downhill_deg": self.downhill_deg, "deer_approach_deg": self.deer_approach_deg,
            "visibility_m": self.visibility_m,
            "terrain": json.loads(self.terrain_json) if self.terrain_json else None,
        }
