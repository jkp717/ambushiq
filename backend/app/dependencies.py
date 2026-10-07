"""Global FastAPI dependencies shared across feature routers."""
from __future__ import annotations

from app.core.security import require_token, require_token_or_query
from app.regions.deps import get_active_region_id

__all__ = ["require_token", "require_token_or_query", "get_active_region_id"]
