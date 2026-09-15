"""Pydantic validation schemas for deer sign."""
from __future__ import annotations

from pydantic import BaseModel


class DeerSignIn(BaseModel):
    kind: str          # "scrape" | "rub"
    lat: float
    lon: float
    is_active: bool = True

