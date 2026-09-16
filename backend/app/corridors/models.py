"""Database ORM models for corridors."""
from __future__ import annotations

import json
from typing import Optional

from sqlalchemy import Float, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class Corridor(Base):
    __tablename__ = "corridors"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    region_id: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    is_active: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    # usage frequency 1 (rarely used) – 10 (heavily used); scales proximity contribution
    usage: Mapped[int] = mapped_column(Integer, default=5, server_default="5")
    # per-corridor falloff distance in metres; NULL → use global falloff_corridor setting
    falloff_m: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    # total corridor width in metres; NULL/0 -> treated as a thin travel line
    width_m: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    # polyline as JSON list of [lat, lon] points
    points_json: Mapped[str] = mapped_column(Text)

    def to_dict(self) -> dict:
        return {"id": self.id, "name": self.name,
                "is_active": bool(self.is_active if self.is_active is not None else 1),
                "usage": self.usage if self.usage is not None else 5,
                "falloff_m": self.falloff_m,
                "width_m": self.width_m,
                "points": json.loads(self.points_json)}
