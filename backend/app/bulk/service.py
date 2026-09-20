"""Bulk activate / deactivate / delete across the map's feature types.

Scouting suggestions have no is_active flag: "dismissed" is their inactive state, so
activate/deactivate map to status new/dismissed for them."""
from __future__ import annotations

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from app.bulk.schemas import BulkItem
from app.cameras.models import Camera
from app.corridors.models import Corridor
from app.deer_sign.models import DeerSign
from app.scouting.models import ScoutingSuggestion
from app.stands.models import Stand
from app.zones.models import Zone

KIND_MODELS = {
    "stand": Stand, "zone": Zone, "corridor": Corridor,
    "sign": DeerSign, "suggestion": ScoutingSuggestion,
}


def apply_bulk(s: Session, region_id: int, items: list[BulkItem], action: str) -> dict:
    """Apply `action` to the given items, touching only rows in `region_id` (ids from other
    regions or that don't exist are ignored). One transaction."""
    ids_by_kind: dict[str, set[int]] = {}
    for it in items:
        ids_by_kind.setdefault(it.kind, set()).add(it.id)

    affected = {kind: 0 for kind in KIND_MODELS}
    cameras_unassigned = 0
    for kind, ids in ids_by_kind.items():
        model = KIND_MODELS[kind]
        match = (model.region_id == region_id, model.id.in_(list(ids)))
        if action == "delete":
            if kind == "stand":
                # don't leave cameras pointing at stands that no longer exist
                stand_ids = list(s.scalars(select(Stand.id).where(*match)))
                if stand_ids:
                    cameras_unassigned = s.execute(
                        update(Camera).where(Camera.stand_id.in_(stand_ids)).values(stand_id=None)).rowcount
            result = s.execute(delete(model).where(*match))
        else:
            active = action == "activate"
            values = ({"status": "new" if active else "dismissed"} if kind == "suggestion"
                      else {"is_active": 1 if active else 0})
            result = s.execute(update(model).where(*match).values(**values))
        affected[kind] = result.rowcount
    s.commit()
    return {"ok": True, "affected": affected, "cameras_unassigned": cameras_unassigned}
