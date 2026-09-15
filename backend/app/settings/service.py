"""Business logic for reading/deriving app settings."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import DEFAULT_SETTINGS
from app.core.database import engine
from app.settings.models import Setting


def get_settings() -> dict:
    with Session(engine) as s:
        rows = {}
        for r in s.scalars(select(Setting)).all():
            val = r.value
            if r.key in DEFAULT_SETTINGS:
                def_val = DEFAULT_SETTINGS[r.key]
                if isinstance(def_val, float):
                    try:
                        rows[r.key] = float(val)
                    except (ValueError, TypeError):
                        rows[r.key] = def_val
                elif isinstance(def_val, int):
                    try:
                        rows[r.key] = int(float(val))
                    except (ValueError, TypeError):
                        rows[r.key] = def_val
                else:
                    rows[r.key] = str(val)
            else:
                rows[r.key] = val
    return {**DEFAULT_SETTINGS, **rows}


def _thermal_params(settings: dict) -> dict:
    return {
        "wind_half_scale": settings.get("thermal_wind_half_scale", 7.0),
        "wind_exponent": settings.get("thermal_wind_exponent", 1.8),
        "midday_discount": settings.get("thermal_midday_discount", 0.3),
    }


