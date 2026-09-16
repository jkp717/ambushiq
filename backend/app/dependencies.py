"""Global FastAPI dependencies shared across feature routers."""
from __future__ import annotations

from app.core.security import require_token
from app.regions.deps import get_active_region_id

__all__ = ["require_token", "get_active_region_id"]
