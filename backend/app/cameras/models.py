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
    region_id: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str] = mapped_column(String(120))
    brand: Mapped[str] = mapped_column(String(32))
    provider_ref: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)  # e.g. Spypoint cam ID
    credentials_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # encrypted
    stand_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # FK stands.id
    is_active: Mapped[int] = mapped_column(Integer, default=1)
    is_deleted: Mapped[int] = mapped_column(Integer, default=0)  # soft-delete: skip on re-discover
    last_sync_at: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)   # when the last sync finished (display only)
    # Everything before this moment is known to be fully handled. Syncs restart from here minus an overlap,
    # and it holds back at the earliest photo whose download failed so that photo is retried.
    sync_cursor_at: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    created_at: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    # provider-reported health, refreshed on every sync — used to avoid
    # penalizing a stand when its camera simply can't capture/transmit
    # anything right now (dead/offline, or over its photo plan for the cycle)
    last_seen_at: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    photo_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    photo_limit: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    # 1 once this camera's stored photo times have been re-checked after the 3.37.2 fix (SpyPoint's
    # capture time is the camera's local clock, not UTC). Until then the next sync reaches back over
    # every stored photo and corrects their times.
    times_rechecked: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    # Provider-reported camera status, refreshed with the health fields above (cameras.service
    # apply_camera_status). Best-effort: None wherever the brand doesn't report it. Drives the camera
    # alerts (battery, signal, SD card) and the status line on Camera Setup.
    battery_pct: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    battery_level: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)   # low / medium / high
    power_type: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)      # AA / SOLAR / ...
    signal_pct: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    signal_bars: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    low_signal: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)         # 1 = the brand flags it weak
    sd_used_mb: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    sd_size_mb: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    temperature_f: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    model: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    def status_dict(self) -> dict:
        return {"battery_pct": self.battery_pct, "battery_level": self.battery_level, "power_type": self.power_type,
                "signal_pct": self.signal_pct, "signal_bars": self.signal_bars,
                "low_signal": None if self.low_signal is None else bool(self.low_signal),
                "sd_used_mb": self.sd_used_mb, "sd_size_mb": self.sd_size_mb,
                "temperature_f": self.temperature_f, "model": self.model}

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
            "status": self.status_dict(),
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
    # Stable identity of the provider's photo (its id, else the image URL without the signed query), so
    # photos stamped in the same second are not mistaken for duplicates. NULL on rows from before v3.11.
    provider_photo_id: Mapped[Optional[str]] = mapped_column(String(400), nullable=True)
    # 0 = the detector found no animal; the photo is kept (so nothing silently vanishes) but is excluded
    # from scoring and the activity graph and hidden from the gallery unless asked for.
    is_animal: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    # 1 = the user favorited this photo: the nightly cleanup and camera deletion never remove it (only
    # deleting its whole region does), and it stays in the gallery even after its camera is deleted.
    is_favorite: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    created_at: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)

    def to_dict(self) -> dict:
        """The sighting itself. Its deer (boxes + buck marks) are added in bulk by the gallery —
        see cameras/bucks.py `attach_detections` — rather than queried per row here."""
        img = None
        if self.image_path and os.path.exists(self.image_path):
            img = f"/api/camera-sightings/{self.id}/image"
        return {
            "id": self.id, "stand_id": self.stand_id, "camera_id": self.camera_id,
            "timestamp": self.timestamp, "confidence_score": self.confidence_score,
            "species": self.species, "species_confidence": self.species_confidence,
            "is_animal": bool(self.is_animal), "is_favorite": bool(self.is_favorite),
            "image_url": img, "created_at": self.created_at,
        }


class DeerDetection(Base):
    """One animal box in a photo (a photo can hold several deer). White-tailed deer carry a feature
    vector for the buck model; the vector is kept after the photo file is cleaned up, so every buck
    label keeps counting toward training."""
    __tablename__ = "deer_detections"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    sighting_id: Mapped[int] = mapped_column(Integer, index=True)
    box_json: Mapped[str] = mapped_column(Text)            # [x1, y1, x2, y2] as fractions of the image
    det_conf: Mapped[float] = mapped_column(Float, default=0.0)
    species: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    vec: Mapped[Optional[str]] = mapped_column(Text, nullable=True)            # base64 float16 feature vector
    vec_source: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)  # which network made it
    buck_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)  # NULL while no model is on
    label: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)    # "buck" | "not_buck" | NULL
    label_source: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)  # "trainer" | "viewer"
    labeled_at: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
