"""Database ORM models for OpenStreetMap trails (foot, horse, two-track) imported from Geofabrik state extracts."""
from __future__ import annotations

from typing import Optional

from sqlalchemy import Float, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class OsmExtract(Base):
    """One Geofabrik extract (a US state) that some region needs. `status`: queued → importing → ok (or failed, with
    `error`; a failed re-import keeps the previous cells). `polygon_json` is the extract's boundary (from Geofabrik's
    index) so the map can tell "no trails mapped here" from "this state isn't downloaded"."""
    __tablename__ = "osm_extracts"
    extract_id: Mapped[str] = mapped_column(String(80), primary_key=True)   # e.g. "us/illinois"
    pbf_url: Mapped[str] = mapped_column(String(300))
    polygon_json: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default="queued")
    imported_at: Mapped[Optional[float]] = mapped_column(Float, nullable=True)   # epoch seconds of the last success
    way_count: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)


class OsmTrailName(Base):
    """Map search's index of named OSM trails (app.search): one row per named trail per connected stretch (two far-apart
    trails sharing a name are separate rows), built from a state's cells after each import (importer.build_name_index).
    `feature_ids` / `cell_keys` (JSON lists) say which stored features make up the trail, to draw it when picked."""
    __tablename__ = "osm_trail_names"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    extract_id: Mapped[str] = mapped_column(String(80), index=True)
    name: Mapped[str] = mapped_column(String(200))
    name_lc: Mapped[str] = mapped_column(String(200), index=True)
    kind: Mapped[str] = mapped_column(String(16))
    lat: Mapped[float] = mapped_column(Float)
    lon: Mapped[float] = mapped_column(Float)
    west: Mapped[float] = mapped_column(Float)
    south: Mapped[float] = mapped_column(Float)
    east: Mapped[float] = mapped_column(Float)
    north: Mapped[float] = mapped_column(Float)
    feature_ids: Mapped[str] = mapped_column(Text)
    cell_keys: Mapped[str] = mapped_column(Text)


class OsmTrailCell(Base):
    """One 0.25° grid cell's simplified OSM trail lines from one extract, as a JSON list of GeoJSON features.
    Key: "osm:{extract_id}:{ix}:{iy}" (a cell on a state line has a row per state)."""
    __tablename__ = "osm_trail_cells"
    key: Mapped[str] = mapped_column(String(120), primary_key=True)
    extract_id: Mapped[str] = mapped_column(String(80), index=True)
    payload: Mapped[str] = mapped_column(Text)
