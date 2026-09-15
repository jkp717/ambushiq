"""Database ORM models for zones (bedding/food)."""
from __future__ import annotations

import json
from typing import Optional

from sqlalchemy import Float, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class Zone(Base):
    __tablename__ = "zones"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    kind: Mapped[str] = mapped_column(String(16))  # "bedding" | "food"
    name: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    lat: Mapped[float] = mapped_column(Float)
    lon: Mapped[float] = mapped_column(Float)
    radius_m: Mapped[int] = mapped_column(Integer, default=80)
    is_active: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    # food-zone quality 1 (poor) – 10 (premium); scales proximity contribution via steeper curve
    quality: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    def to_dict(self) -> dict:
        return {"id": self.id, "kind": self.kind, "name": self.name,
                "lat": self.lat, "lon": self.lon, "radius_m": self.radius_m,
                "is_active": bool(self.is_active if self.is_active is not None else 1),
                "quality": self.quality}
