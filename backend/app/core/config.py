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
    # summed, confidence-weighted deer-photo "evidence" (each qualifying photo
    # contributes 0.1-1.0) needed in a period to reach the full boost above —
    # see scoring.camera_boost / scoring.CAMERA_BOOST_SATURATION_DEFAULT
    "camera_boost_saturation": 3.0,
    "max_camera_penalty_pct": 15.0,
    "camera_lookback_hours": 72.0,
    "camera_health_max_age_hours": 48.0,
    "camera_image_dir": CAMERA_IMAGE_DIR,
    # weather source: primary provider (+ its API key, if it needs one) and an
    # optional secondary provider used only to backfill fields the primary
    # doesn't report (most commonly solar radiation, for the thermal model)
    "weather_provider": "open_meteo",
    "weather_provider_api_key": "",
    "weather_secondary_provider": "",
    "weather_secondary_provider_api_key": "",
    # v2.28: Scouting Suggestions — terrain/land-cover analysis of a user-drawn
    # circle that flags candidate locations to go scout in person
    "scout_radius_default_m": 800.0,
    "scout_radius_min_m": 60.0,
    "scout_radius_max_m": 2400.0,          # ~1.5 mi
    "scout_grid_n": 60,                    # fixed sample-grid dimension (NxN), independent of radius
    "scout_steep_slope_pct": 20.0,         # slope% treated as a "wall" for pinch-point detection
    "scout_max_pinch_width_m": 120.0,      # widest gap between two walls still called a pinch point
    "scout_min_candidate_score": 40.0,     # floor before a grid cell is considered at all (0-100 scale)
    "scout_min_separation_m": 150.0,       # non-max-suppression radius when clustering candidates
    "scout_max_suggestions_per_run": 8,
    "scout_suggestion_radius_m": 60.0,     # radius of each persisted suggestion's map marker
    "scout_overlap_skip_threshold": 0.5,   # "merge" mode: skip a candidate overlapping an existing one by more than this fraction
    "scout_weight_terrain": 0.55,
    "scout_weight_proximity": 0.20,
    "scout_weight_camera": 0.15,
    "scout_weight_unexplored": 0.10,
}
