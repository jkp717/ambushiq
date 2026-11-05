"""Database ORM model for terrain-tile prefetch requests (app.tiles.prefetch)."""
from __future__ import annotations

from typing import Optional

from sqlalchemy import Float, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class TilePrefetchArea(Base):
    """An area someone asked the server to fetch terrain tiles for overnight (from the offline-maps panel).
    status: queued -> running -> ready (every block cached) | partial (some blocks failed, or the night's run ran
    out of time; the next night carries on)."""
    __tablename__ = "tile_prefetch_areas"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    region_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    label: Mapped[str] = mapped_column(String(120), default="")
    north: Mapped[float] = mapped_column(Float)
    south: Mapped[float] = mapped_column(Float)
    west: Mapped[float] = mapped_column(Float)
    east: Mapped[float] = mapped_column(Float)
    layers: Mapped[str] = mapped_column(String(64))          # comma-separated, e.g. "contours,hillshade"
    min_zoom: Mapped[int] = mapped_column(Integer)
    max_zoom: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16), default="queued")
    blocks_total: Mapped[int] = mapped_column(Integer, default=0)
    blocks_done: Mapped[int] = mapped_column(Integer, default=0)      # blocks cached on the server
    blocks_failed: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[str] = mapped_column(String(40), default="")
    finished_at: Mapped[str] = mapped_column(String(40), default="")

    def to_dict(self) -> dict:
        return {
            "id": self.id, "region_id": self.region_id, "label": self.label,
            "bounds": {"north": self.north, "south": self.south, "west": self.west, "east": self.east},
            "layers": [l for l in self.layers.split(",") if l], "min_zoom": self.min_zoom, "max_zoom": self.max_zoom,
            "status": self.status, "blocks_total": self.blocks_total, "blocks_done": self.blocks_done,
            "blocks_failed": self.blocks_failed, "created_at": self.created_at, "finished_at": self.finished_at,
        }
