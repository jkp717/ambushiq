"""Database ORM models for deer sign (scrapes/rubs)."""
from __future__ import annotations

from sqlalchemy import Float, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class DeerSign(Base):
    __tablename__ = "deer_sign"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    kind: Mapped[str] = mapped_column(String(20))          # "scrape" | "rub"
    name: Mapped[str] = mapped_column(String(120))          # auto-generated
    lat: Mapped[float] = mapped_column(Float)
    lon: Mapped[float] = mapped_column(Float)
    is_active: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    created_at: Mapped[str] = mapped_column(String(32), default="")

    def to_dict(self):
        return {"id": self.id, "kind": self.kind, "name": self.name,
                "lat": self.lat, "lon": self.lon,
                "is_active": bool(self.is_active if self.is_active is not None else 1),
                "created_at": self.created_at}
