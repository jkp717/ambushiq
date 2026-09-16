"""Database ORM models for scouting suggestions."""
from __future__ import annotations

import json
from typing import Optional

from sqlalchemy import Float, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class ScoutingSuggestion(Base):
    __tablename__ = "scouting_suggestions"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    lat: Mapped[float] = mapped_column(Float)
    lon: Mapped[float] = mapped_column(Float)
    radius_m: Mapped[float] = mapped_column(Float, default=60.0)
    score: Mapped[float] = mapped_column(Float)  # 0-100
    reasoning_json: Mapped[str] = mapped_column(Text)  # {"breakdown":[...], "text": "..."}
    status: Mapped[str] = mapped_column(String(16), default="new", server_default="new")  # "new" | "dismissed"
    # the analysis circle that produced this suggestion — needed for overlap dedup
    request_lat: Mapped[float] = mapped_column(Float)
    request_lon: Mapped[float] = mapped_column(Float)
    request_radius_m: Mapped[float] = mapped_column(Float)
    created_at: Mapped[str] = mapped_column(String(32), default="")

    def to_dict(self) -> dict:
        try:
            reasoning = json.loads(self.reasoning_json) if self.reasoning_json else {"breakdown": [], "text": ""}
        except (ValueError, TypeError):
            reasoning = {"breakdown": [], "text": ""}
        return {
            "id": self.id, "lat": self.lat, "lon": self.lon, "radius_m": self.radius_m,
            "score": self.score, "reasoning": reasoning, "status": self.status,
            "request_lat": self.request_lat, "request_lon": self.request_lon,
            "request_radius_m": self.request_radius_m, "created_at": self.created_at,
        }
