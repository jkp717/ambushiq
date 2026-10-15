"""Pydantic validation schemas for alerts."""
from __future__ import annotations

from pydantic import BaseModel, Field


class DismissIn(BaseModel):
    key: str = Field(min_length=1, max_length=200)
