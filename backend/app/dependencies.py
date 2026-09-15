"""Global FastAPI dependencies shared across feature routers."""
from __future__ import annotations

from app.core.security import require_token

__all__ = ["require_token"]
