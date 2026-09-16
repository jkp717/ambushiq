"""Route handlers for /api/settings."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.database import engine
from app.core.security import decrypt_settings_key, encrypt_settings_key
from app.dependencies import require_token
from app.scheduler import _reschedule_sync
from app.settings.models import Setting
from app.settings.schemas import SettingsIn
from app.settings.service import get_settings

router = APIRouter(tags=["settings"])

# Settings stored encrypted (like camera credentials) — never sent back to the
# frontend as plaintext; read_settings/write_settings mask these to an
# "<x>_set" boolean instead.
_API_KEY_FIELDS = ("weather_provider_api_key", "weather_secondary_provider_api_key")


def _mask_api_keys(result: dict) -> dict:
    result = dict(result)
    for field in _API_KEY_FIELDS:
        result[f"{field}_set"] = bool(decrypt_settings_key(result.get(field)))
        result.pop(field, None)
    return result


@router.get("/api/settings")
def read_settings(_=Depends(require_token)):
    return _mask_api_keys(get_settings())


@router.put("/api/settings")
def write_settings(body: SettingsIn, _=Depends(require_token)):
    with Session(engine) as s:
        for k, v in body.model_dump().items():
            if v is None:
                continue
            if k in _API_KEY_FIELDS:
                str_val = encrypt_settings_key(v) if v else ""
            else:
                str_val = str(v)
            row = s.get(Setting, k)
            if row:
                row.value = str_val
            else:
                s.add(Setting(key=k, value=str_val))
        s.commit()
    result = _mask_api_keys(get_settings())
    # Live-reschedule the sync job when its interval changes — no restart required.
    if body.camera_sync_interval_minutes is not None:
        _reschedule_sync(int(body.camera_sync_interval_minutes) or 30)
    return result
