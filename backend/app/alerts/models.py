"""Database ORM model for dismissed alerts."""
from __future__ import annotations

from sqlalchemy import Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class AlertDismissal(Base):
    """An alert the user dismissed in a region. Kept only while that alert is still active: once its
    condition clears the row is deleted (alerts.service), so the alert shows again if it comes back."""
    __tablename__ = "alert_dismissals"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    region_id: Mapped[int] = mapped_column(Integer, index=True)
    key: Mapped[str] = mapped_column(String(200))
    dismissed_at: Mapped[str] = mapped_column(String(40))
