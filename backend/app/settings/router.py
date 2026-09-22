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

# Weather-provider API keys are stored encrypted (like camera credentials),
# one row per provider — key name "weather_api_key__<provider_id>" — so
# switching providers can never reuse another provider's stored key. Legacy
# single-slot fields from before per-provider storage existed.
_WEATHER_KEY_PREFIX = "weather_api_key__"
_LEGACY_API_KEY_FIELDS = ("weather_provider_api_key", "weather_secondary_provider_api_key")


def _mask_api_keys(result: dict) -> dict:
    keys_set = {
        k[len(_WEATHER_KEY_PREFIX):]: True
        for k, v in result.items()
        if k.startswith(_WEATHER_KEY_PREFIX) and decrypt_settings_key(v)
    }
    result = {k: v for k, v in result.items() if not k.startswith(_WEATHER_KEY_PREFIX) and k not in _LEGACY_API_KEY_FIELDS}
    result["weather_provider_api_keys_set"] = keys_set
    return result


def _migrate_legacy_keys(s: Session, settings: dict) -> None:
    """One-time best-effort migration: an old flat stored key is attributed to
    whichever provider is currently selected in that slot, since the old
    schema couldn't record which provider it belonged to."""
    legacy_primary = settings.get("weather_provider_api_key")
    legacy_secondary = settings.get("weather_secondary_provider_api_key")
    if not legacy_primary and not legacy_secondary:
        return
    if legacy_primary:
        provider_id = settings.get("weather_provider") or "open_meteo"
        new_key = f"{_WEATHER_KEY_PREFIX}{provider_id}"
        if not s.get(Setting, new_key):
            s.add(Setting(key=new_key, value=legacy_primary))
        row = s.get(Setting, "weather_provider_api_key")
        if row:
            s.delete(row)
    if legacy_secondary:
        provider_id = settings.get("weather_secondary_provider") or ""
        if provider_id:
            new_key = f"{_WEATHER_KEY_PREFIX}{provider_id}"
            if not s.get(Setting, new_key):
                s.add(Setting(key=new_key, value=legacy_secondary))
        row = s.get(Setting, "weather_secondary_provider_api_key")
        if row:
            s.delete(row)
    s.commit()


@router.get("/api/settings")
def read_settings(_=Depends(require_token)):
    settings = get_settings()
    if settings.get("weather_provider_api_key") or settings.get("weather_secondary_provider_api_key"):
        with Session(engine) as s:
            _migrate_legacy_keys(s, settings)
        settings = get_settings()
    return _mask_api_keys(settings)


@router.put("/api/settings")
def write_settings(body: SettingsIn, _=Depends(require_token)):
    data = body.model_dump()
    weather_provider_api_keys = data.pop("weather_provider_api_keys", None) or {}
    with Session(engine) as s:
        for k, v in data.items():
            if v is None:
                continue
            row = s.get(Setting, k)
            str_val = str(v)
            if row:
                row.value = str_val
            else:
                s.add(Setting(key=k, value=str_val))
        for provider_id, key_val in weather_provider_api_keys.items():
            setting_key = f"{_WEATHER_KEY_PREFIX}{provider_id}"
            str_val = encrypt_settings_key(key_val) if key_val else ""
            row = s.get(Setting, setting_key)
            if row:
                row.value = str_val
            else:
                s.add(Setting(key=setting_key, value=str_val))
        s.commit()
    result = _mask_api_keys(get_settings())
    # Live-reschedule the sync job when its interval changes — no restart required.
    if body.camera_sync_interval_minutes is not None:
        _reschedule_sync(int(body.camera_sync_interval_minutes) or 30)
    return result
