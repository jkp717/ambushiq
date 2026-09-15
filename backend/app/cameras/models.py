"""Database ORM models for trail cameras and sightings."""
from __future__ import annotations

import os
from typing import Optional

from sqlalchemy import Float, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class Camera(Base):
    __tablename__ = "cameras"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(120))
    brand: Mapped[str] = mapped_column(String(32))
    provider_ref: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)  # e.g. Spypoint cam ID
    credentials_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # encrypted
    stand_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # FK stands.id
    is_active: Mapped[int] = mapped_column(Integer, default=1)
    is_deleted: Mapped[int] = mapped_column(Integer, default=0)  # soft-delete: skip on re-discover
    last_sync_at: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    created_at: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    # provider-reported health, refreshed on every sync — used to avoid
    # penalizing a stand when its camera simply can't capture/transmit
    # anything right now (dead/offline, or over its photo plan for the cycle)
    last_seen_at: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    photo_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    photo_limit: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    def to_dict(self) -> dict:
        # NEVER expose credentials
        return {
            "id": self.id, "name": self.name, "brand": self.brand,
            "provider_ref": self.provider_ref,
            "stand_id": self.stand_id, "is_active": bool(self.is_active),
            "last_sync_at": self.last_sync_at, "created_at": self.created_at,
            "has_credentials": bool(self.credentials_json),
            "last_seen_at": self.last_seen_at,
            "photo_count": self.photo_count, "photo_limit": self.photo_limit,
        }


class CameraSighting(Base):
    __tablename__ = "camera_sightings"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    stand_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    camera_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    timestamp: Mapped[str] = mapped_column(String(32))  # ISO of the sighting
    confidence_score: Mapped[float] = mapped_column(Float, default=0.0)
    species: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    species_confidence: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    image_path: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # NULL after cleanup
    created_at: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)

    def to_dict(self) -> dict:
        img = None
        if self.image_path and os.path.exists(self.image_path):
            img = f"/api/camera-sightings/{self.id}/image"
        return {
            "id": self.id, "stand_id": self.stand_id, "camera_id": self.camera_id,
            "timestamp": self.timestamp, "confidence_score": self.confidence_score,
            "species": self.species, "species_confidence": self.species_confidence,
            "image_url": img, "created_at": self.created_at,
        }
