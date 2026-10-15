"""Route handlers for the alerts bell."""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends

from app.alerts import service
from app.alerts.schemas import DismissIn
from app.dependencies import get_active_region_id, require_token

router = APIRouter(prefix="/api/alerts", tags=["alerts"])


@router.get("")
def list_alerts(region_id: int = Depends(get_active_region_id), _=Depends(require_token)):
    return {"alerts": service.current_alerts(region_id),
            "generated_at": datetime.now(timezone.utc).isoformat()}


@router.post("/dismiss")
def dismiss_alert(body: DismissIn, region_id: int = Depends(get_active_region_id), _=Depends(require_token)):
    service.dismiss(region_id, body.key)
    return {"ok": True}
