"""Environment variables & app-wide settings defaults."""
from __future__ import annotations

import os

DB_URL = os.environ.get("DATABASE_URL", "postgresql+psycopg://ambush:ambush@db:5432/ambushiq")
APP_TOKEN = os.environ.get("APP_TOKEN", "")  # shared secret; required in prod


def _read_version() -> str:
    backend_dir = os.path.join(os.path.dirname(__file__), "..", "..")
    for p in ("/app/VERSION",
              os.path.join(backend_dir, "..", "VERSION"),
              os.path.join(backend_dir, "VERSION")):
        try:
            with open(p) as f:
                return f.read().strip()
        except OSError:
            continue
    return "unknown"


APP_VERSION = _read_version()

# v2.15: trail cameras. credentials_json holds a Fernet-encrypted blob (never plaintext).
CAMERA_BRANDS = ("spypoint", "reveal", "moultrie", "stealth_cam", "browning", "spartan")

CAMERA_IMAGE_DIR = os.environ.get("CAMERA_IMAGE_DIR", "/app/data/camera_images")

# home/hunt region center — stored separately; absent until the user sets it
HOME_KEYS = ("home_lat", "home_lon")

# default proximity weights + falloffs (meters)
DEFAULT_SETTINGS = {
    "weight_corridor": 0.15, "falloff_corridor": 150,
    "weight_food": 0.15, "falloff_food": 200,
    "weight_bedding": 0.10, "falloff_bedding": 250,
    "weight_scrape": 0.12, "falloff_scrape": 100,
    "weight_rub":    0.10, "falloff_rub":    80,
    # deer day-rating weather factor weights (relative; normalized at use)
    "rate_w_pressure": 0.32, "rate_w_wind": 0.20, "rate_w_rain": 0.28, "rate_w_temp": 0.20,
    # thermal coherence model (scoring.thermal_coherence) — how much of a thermal's
    # directional potential survives into the scent blend vs. being overwhelmed by
    # ambient wind / midday convective mixing
    "thermal_wind_half_scale": 7.0,
    "thermal_wind_exponent": 1.8,
    "thermal_midday_discount": 0.3,
    # v2.15: trail-camera + rut-date settings
    "camera_sync_interval_minutes": 30,
    "image_retention_days": 60,
    "camera_backfill_days": 7,
    "max_camera_boost_pct": 15.0,
    "max_camera_penalty_pct": 15.0,
    "camera_lookback_hours": 72.0,
    "camera_health_max_age_hours": 48.0,
    "rut_peak_month": 12,
    "rut_peak_day": 5,
    "camera_image_dir": CAMERA_IMAGE_DIR,
    "property_timezone": "America/Chicago",
    # weather source: primary provider (+ its API key, if it needs one) and an
    # optional secondary provider used only to backfill fields the primary
    # doesn't report (most commonly solar radiation, for the thermal model)
    "weather_provider": "open_meteo",
    "weather_provider_api_key": "",
    "weather_secondary_provider": "",
    "weather_secondary_provider_api_key": "",
}
