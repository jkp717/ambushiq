"""Database ORM model for job runs (manual and scheduled), shown in Settings → Jobs."""
from __future__ import annotations

from typing import Optional

from sqlalchemy import Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class JobRun(Base):
    __tablename__ = "job_runs"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(String(64), index=True)
    region_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)   # region-scoped jobs only
    trigger: Mapped[str] = mapped_column(String(16))            # "manual" | "scheduled"
    status: Mapped[str] = mapped_column(String(16))             # "running" | "success" | "partial" | "failed"
    started_at: Mapped[str] = mapped_column(String(40))
    finished_at: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    total: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)   # items to process, when known
    done: Mapped[int] = mapped_column(Integer, default=0)
    failed: Mapped[int] = mapped_column(Integer, default=0)
    current: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)   # item being processed
    message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    errors_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)      # [{"item", "error"}, ...]
