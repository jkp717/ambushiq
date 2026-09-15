"""Pydantic validation schemas for trail cameras."""
from __future__ import annotations

from pydantic import BaseModel


class CameraIn(BaseModel):
    name: str
    brand: str
    stand_id: int | None = None
    credentials: dict | None = None  # plaintext in; stored encrypted


class CameraUpdateIn(BaseModel):
    name: str | None = None
    stand_id: int | None = None
    is_active: bool | None = None
    credentials: dict | None = None


class CameraDiscoverIn(BaseModel):
    brand: str
    credentials: dict
    # None => preview/dry-run (classify only, no writes). Provided => apply,
    # keyed by provider_ref, true meaning "include this camera" (also un-skips
    # a previously-removed one); omitted refs default to included.
    selections: dict[str, bool] | None = None
