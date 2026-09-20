"""Pydantic validation schemas for bulk feature actions."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

FeatureKind = Literal["stand", "zone", "corridor", "sign", "suggestion"]


class BulkItem(BaseModel):
    kind: FeatureKind
    id: int


class BulkIn(BaseModel):
    items: list[BulkItem] = Field(max_length=5000)
    action: Literal["activate", "deactivate", "delete"]
