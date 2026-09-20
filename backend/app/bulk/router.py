"""Route handlers for /api/features/bulk."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.bulk import service
from app.bulk.schemas import BulkIn
from app.core.database import engine
from app.dependencies import get_active_region_id, require_token

router = APIRouter(prefix="/api/features", tags=["bulk"])


@router.post("/bulk")
def bulk_update(body: BulkIn, region_id: int = Depends(get_active_region_id), _=Depends(require_token)):
    """Activate, deactivate or delete many map features (stands, zones, corridors, rubs and
    scrapes, scouting suggestions) at once, within the active region."""
    if not body.items:
        return {"ok": True, "affected": {k: 0 for k in service.KIND_MODELS}, "cameras_unassigned": 0}
    with Session(engine) as s:
        return service.apply_bulk(s, region_id, body.items, body.action)
