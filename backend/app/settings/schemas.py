"""Pydantic validation schemas for app settings & home location."""
from __future__ import annotations

from pydantic import BaseModel


class SettingsIn(BaseModel):
    weight_corridor: float | None = None
    falloff_corridor: float | None = None
    weight_food: float | None = None
    falloff_food: float | None = None
    weight_bedding: float | None = None
    falloff_bedding: float | None = None
    weight_scrape: float | None = None
    falloff_scrape: float | None = None
    weight_rub: float | None = None
    falloff_rub: float | None = None
    rate_w_pressure: float | None = None
    rate_w_wind: float | None = None
    rate_w_rain: float | None = None
    rate_w_temp: float | None = None
    max_camera_boost_pct: float | None = None
    camera_boost_saturation: float | None = None
    max_camera_penalty_pct: float | None = None
    camera_lookback_hours: float | None = None
    camera_health_max_age_hours: float | None = None
    camera_sync_interval_minutes: float | None = None
    image_retention_days: float | None = None
    camera_backfill_days: float | None = None
    rut_peak_month: float | None = None
    rut_peak_day: float | None = None
    camera_image_dir: str | None = None
    property_timezone: str | None = None
    thermal_wind_half_scale: float | None = None
    thermal_wind_exponent: float | None = None
    thermal_midday_discount: float | None = None
    weather_provider: str | None = None
    weather_provider_api_key: str | None = None
    weather_secondary_provider: str | None = None
    weather_secondary_provider_api_key: str | None = None


class HomeIn(BaseModel):
    lat: float
    lon: float
