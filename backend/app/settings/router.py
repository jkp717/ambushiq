"""Route handlers for /api/settings and /api/home."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import engine
from app.core.config import HOME_KEYS
from app.core.security import decrypt_settings_key, encrypt_settings_key
from app.dependencies import require_token
from app.scheduler import _reschedule_cleanup_tz, _reschedule_sync
from app.settings.models import Setting
from app.settings.schemas import HomeIn, SettingsIn
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
    # Live-reschedule jobs when relevant settings change — no restart required.
    if body.camera_sync_interval_minutes is not None:
        _reschedule_sync(int(body.camera_sync_interval_minutes) or 30)
    if body.property_timezone is not None:
        _reschedule_cleanup_tz(body.property_timezone)
    return result



@router.get("/api/home")
def read_home(_=Depends(require_token)):
    with Session(engine) as s:
        rows = {}
        for r in s.scalars(select(Setting)).all():
            if r.key in HOME_KEYS:
                try:
                    rows[r.key] = float(r.value)
                except (ValueError, TypeError):
                    pass
    if "home_lat" in rows and "home_lon" in rows:
        return {"lat": rows["home_lat"], "lon": rows["home_lon"], "set": True}
    return {"lat": None, "lon": None, "set": False}


@router.put("/api/home")
def write_home(body: HomeIn, _=Depends(require_token)):
    if not (-90 <= body.lat <= 90 and -180 <= body.lon <= 180):
        raise HTTPException(400, "lat/lon out of range")
    with Session(engine) as s:
        for k, v in (("home_lat", body.lat), ("home_lon", body.lon)):
            str_val = str(v)
            row = s.get(Setting, k)
            if row:
                row.value = str_val
            else:
                s.add(Setting(key=k, value=str_val))
        s.commit()
    return {"lat": body.lat, "lon": body.lon, "set": True}
