"""Route handlers for trail cameras, sightings, and camera images."""
from __future__ import annotations

import logging
import os
from datetime import date, datetime, timezone
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from app.cameras import activity
from app.cameras import providers as cameras_mod
from app.cameras.models import Camera, CameraSighting
from app.cameras.schemas import CameraDiscoverIn, CameraIn, CameraUpdateIn
from app.cameras.service import (
    _backfill_species_task,
    _sync_one_camera_task,
    get_camera_dir,
)
from app.core.config import CAMERA_BRANDS, CAMERA_IMAGE_DIR
from app.core.database import engine
from app.core.security import decrypt_credentials, encrypt_credentials
from app.dependencies import get_active_region_id, require_token
from app.forecast.service import _camera_health
from app.regions.service import get_region_dict
from app.settings.service import get_settings

log = logging.getLogger(__name__)

router = APIRouter(tags=["cameras"])


@router.get("/api/camera-sightings/{sighting_id}/image")
def get_sighting_image(sighting_id: int):
    with Session(engine) as s:
        sighting = s.get(CameraSighting, sighting_id)
        if not sighting or not sighting.image_path or not os.path.exists(sighting.image_path):
            raise HTTPException(404, "image not found")
        return FileResponse(sighting.image_path)


@router.get("/static/camera_images/{subpath:path}")
def get_static_camera_image(subpath: str):
    settings = get_settings()
    base_dir = str(settings.get("camera_image_dir") or CAMERA_IMAGE_DIR)
    candidate = os.path.join(base_dir, subpath)
    if os.path.isfile(candidate):
        return FileResponse(candidate)
    candidate_default = os.path.join(CAMERA_IMAGE_DIR, subpath)
    if os.path.isfile(candidate_default):
        return FileResponse(candidate_default)
    raise HTTPException(404, "image not found")


@router.get("/api/camera-providers")
def camera_providers(_=Depends(require_token)):
    """Brand metadata for the setup wizard (which are implemented + required fields)."""
    return {"providers": cameras_mod.provider_meta()}


@router.get("/api/cameras")
def list_cameras(region_id: int = Depends(get_active_region_id), _=Depends(require_token)):
    max_age = float(get_settings().get("camera_health_max_age_hours", 48.0) or 48.0)
    with Session(engine) as s:
        out = []
        for c in s.scalars(select(Camera).where(
            Camera.is_deleted == 0, Camera.region_id == region_id
        ).order_by(Camera.name)).all():
            d = c.to_dict()
            d["health"] = _camera_health(c.last_seen_at, c.photo_count, c.photo_limit, max_age)
            out.append(d)
        return out


@router.post("/api/cameras")
def create_camera(body: CameraIn, region_id: int = Depends(get_active_region_id), _=Depends(require_token)):
    if body.brand not in CAMERA_BRANDS:
        raise HTTPException(400, "unknown brand")
    now = datetime.now(timezone.utc).isoformat()
    with Session(engine) as s:
        cam = Camera(
            name=body.name, brand=body.brand, stand_id=body.stand_id, is_active=1,
            created_at=now, region_id=region_id,
            credentials_json=encrypt_credentials(body.credentials) if body.credentials else None,
        )
        s.add(cam); s.commit(); s.refresh(cam)
        return cam.to_dict()


@router.post("/api/cameras/discover")
async def discover_cameras(body: CameraDiscoverIn, region_id: int = Depends(get_active_region_id),
                            _=Depends(require_token)):
    """Connect a brand account and list/create/update camera records from the provider.
    With no `selections`, this is a preview/dry-run: cameras are classified
    ("new" / "existing" / "previously_removed") but nothing is written. Pass
    `selections` (provider_ref -> include) to apply — a previously-removed
    camera is only restored (un-skipped) if its ref is selected."""
    try:
        prov = cameras_mod.get_provider(body.brand, body.credentials)
    except cameras_mod.CameraError as e:
        raise HTTPException(400, str(e))
    if not prov.implemented:
        raise HTTPException(501, f"{body.brand} sync is not implemented yet")
    try:
        sp_cameras = await prov.fetch_cameras()
    except cameras_mod.CameraError as e:
        raise HTTPException(400, str(e))

    creds_enc = encrypt_credentials(body.credentials)
    now = datetime.now(timezone.utc).isoformat()

    with Session(engine) as s:
        def classify(ref):
            # Scoped to the active region — the same provider account connected
            # in two different regions must not collide on brand+provider_ref.
            existing = s.scalars(
                select(Camera).where(Camera.brand == body.brand, Camera.provider_ref == ref,
                                      Camera.region_id == region_id)
            ).first()
            if existing and existing.is_deleted:
                return existing, "previously_removed"
            if existing:
                return existing, "existing"
            return None, "new"

        if body.selections is None:
            cameras = [{"provider_ref": sp["id"], "name": sp["name"], "status": classify(sp["id"])[1]}
                       for sp in sp_cameras]
            return {"preview": True, "cameras": cameras}

        created, updated, restored, skipped = [], [], [], []
        for sp in sp_cameras:
            ref, name = sp["id"], sp["name"]
            include = body.selections.get(ref, True)
            existing, status = classify(ref)
            if status == "previously_removed":
                if not include:
                    skipped.append({"name": name, "provider_ref": ref})
                    continue
                existing.is_deleted = 0
                existing.name = name
                existing.credentials_json = creds_enc
                existing.last_seen_at = sp.get("last_seen_at")
                existing.photo_count = sp.get("photo_count")
                existing.photo_limit = sp.get("photo_limit")
                s.commit(); s.refresh(existing)
                restored.append(existing.to_dict())
            elif status == "existing":
                if not include:
                    continue
                changed = existing.name != name or existing.credentials_json != creds_enc
                if changed:
                    existing.name = name
                    existing.credentials_json = creds_enc
                existing.last_seen_at = sp.get("last_seen_at")
                existing.photo_count = sp.get("photo_count")
                existing.photo_limit = sp.get("photo_limit")
                s.commit()
                s.refresh(existing)
                updated.append(existing.to_dict())
            else:
                if not include:
                    continue
                cam = Camera(
                    name=name, brand=body.brand, provider_ref=ref,
                    credentials_json=creds_enc, region_id=region_id,
                    stand_id=None, is_active=1, is_deleted=0, created_at=now,
                    last_seen_at=sp.get("last_seen_at"),
                    photo_count=sp.get("photo_count"), photo_limit=sp.get("photo_limit"),
                )
                s.add(cam)
                s.commit()
                s.refresh(cam)
                created.append(cam.to_dict())

    log.info("discover_cameras: brand=%s created=%d updated=%d restored=%d skipped=%d",
             body.brand, len(created), len(updated), len(restored), len(skipped))
    return {"preview": False, "created": created, "updated": updated, "restored": restored, "skipped": skipped}


@router.put("/api/cameras/{camera_id}")
def update_camera(camera_id: int, body: CameraUpdateIn, region_id: int = Depends(get_active_region_id),
                   _=Depends(require_token)):
    with Session(engine) as s:
        cam = s.get(Camera, camera_id)
        if not cam or cam.region_id != region_id:
            raise HTTPException(404, "not found")
        if body.name is not None:
            cam.name = body.name
        # Use model_fields_set to distinguish "field not sent" from "field sent as null".
        # {"stand_id": null}  → unassign camera from its stand (cam.stand_id = None)
        # {"stand_id": 3}     → assign to stand 3
        # {}                  → don't touch stand_id at all
        if "stand_id" in body.model_fields_set:
            cam.stand_id = body.stand_id
        if body.is_active is not None:
            cam.is_active = 1 if body.is_active else 0
        if body.credentials is not None:
            cam.credentials_json = encrypt_credentials(body.credentials)
        s.commit(); s.refresh(cam)
        return cam.to_dict()


@router.delete("/api/cameras/{camera_id}")
def delete_camera(camera_id: int, delete_images: bool = False,
                   region_id: int = Depends(get_active_region_id), _=Depends(require_token)):
    deleted_dir = None
    with Session(engine) as s:
        cam = s.get(Camera, camera_id)
        if cam and cam.region_id == region_id:
            if delete_images:
                deleted_dir = get_camera_dir(cam.brand, cam.name, cam.region_id)
            # Delete all sightings for this camera
            for sg in s.scalars(select(CameraSighting).where(CameraSighting.camera_id == camera_id)).all():
                s.delete(sg)
            # Soft-delete so this camera is never auto-recreated by discover
            cam.is_deleted = 1
            s.commit()
    if delete_images and deleted_dir:
        import shutil
        if os.path.isdir(deleted_dir):
            try:
                shutil.rmtree(deleted_dir)
                log.info("delete_camera: removed image dir %s", deleted_dir)
            except Exception as exc:
                log.warning("delete_camera: failed to remove %s: %s", deleted_dir, exc)
    return {"ok": True}


@router.post("/api/cameras/{camera_id}/verify")
async def verify_camera(camera_id: int, region_id: int = Depends(get_active_region_id),
                         _=Depends(require_token)):
    """Test stored credentials against the provider."""
    with Session(engine) as s:
        cam = s.get(Camera, camera_id)
        if not cam or cam.region_id != region_id:
            raise HTTPException(404, "not found")
        creds = decrypt_credentials(cam.credentials_json)
        brand = cam.brand
    try:
        prov = cameras_mod.get_provider(brand, creds)
        ok = await prov.verify()
        return {"ok": bool(ok), "implemented": prov.implemented}
    except cameras_mod.NotImplementedProvider as e:
        raise HTTPException(501, str(e))
    except cameras_mod.CameraError as e:
        raise HTTPException(400, str(e))


@router.post("/api/cameras/{camera_id}/sync")
async def sync_camera_now(camera_id: int, bg: BackgroundTasks,
                           days: Optional[int] = Query(default=None, ge=1, le=90),
                           region_id: int = Depends(get_active_region_id), _=Depends(require_token)):
    """Manually trigger a sync for one camera. Returns immediately; sync runs in background.
    With `days`, re-imports that many days of history (photos already stored are skipped)."""
    with Session(engine) as s:
        cam = s.get(Camera, camera_id)
        if not cam or cam.region_id != region_id:
            raise HTTPException(404, "not found")
    bg.add_task(_sync_one_camera_task, camera_id, days)
    return {"ok": True, "status": "running"}


@router.post("/api/cameras/backfill-species")
def backfill_species(bg: BackgroundTasks, region_id: int = Depends(get_active_region_id),
                      _=Depends(require_token)):
    """Reclassify existing sightings that predate species tracking. The
    candidate count is computed here (synchronously) so it's visible in the
    HTTP response right away; the actual reclassification runs in the
    background — check server logs (grep for "backfill_species") for a
    completion summary. Only sightings with their original photo still on
    disk can be reclassified."""
    with Session(engine) as s:
        ids = list(s.scalars(
            select(CameraSighting.id)
            .join(Camera, Camera.id == CameraSighting.camera_id)
            .where(
                CameraSighting.species.is_(None),
                CameraSighting.is_animal == 1,
                CameraSighting.image_path.isnot(None),
                Camera.region_id == region_id,
            )
        ).all())
    log.info("backfill_species: queued — %d candidate sighting(s)", len(ids))
    bg.add_task(_backfill_species_task, ids)
    return {"ok": True, "status": "running", "candidates": len(ids)}


@router.get("/api/cameras/{camera_id}/sightings")
def camera_sightings(
    camera_id: int,
    limit: int = 100,       # max rows returned; capped at 1000
    since: Optional[str] = None,  # ISO timestamp — return only sightings newer than this
    region_id: int = Depends(get_active_region_id),
    _=Depends(require_token),
):
    """Paginated sighting list. Default: 100 most-recent. Use `since` for incremental
    loads (pass the last timestamp you received to get only newer records)."""
    with Session(engine) as s:
        cam = s.get(Camera, camera_id)
        if not cam or cam.region_id != region_id:
            raise HTTPException(404, "not found")
        q = select(CameraSighting).where(CameraSighting.camera_id == camera_id, CameraSighting.is_animal == 1)
        if since:
            q = q.where(CameraSighting.timestamp > since)
        q = q.order_by(CameraSighting.timestamp.desc()).limit(max(1, min(limit, 1000)))
        return [r.to_dict() for r in s.scalars(q).all()]


# ---------- activity + gallery (filterable across cameras) ----------

def _scoped(q, region_id: int, brands: list[str], camera_ids: list[int]):
    """Restrict a sightings query to live cameras in the active region, optionally narrowed by brand / camera."""
    q = q.join(Camera, Camera.id == CameraSighting.camera_id).where(
        Camera.region_id == region_id, Camera.is_deleted == 0)
    if brands:
        q = q.where(Camera.brand.in_(brands))
    if camera_ids:
        q = q.where(Camera.id.in_(camera_ids))
    return q


@router.get("/api/cameras/filters")
def camera_filter_options(region_id: int = Depends(get_active_region_id), _=Depends(require_token)):
    """Animal types that actually appear in this region's photos (for the filter chips)."""
    with Session(engine) as s:
        names = s.scalars(_scoped(select(CameraSighting.species).distinct(), region_id, [], [])
                          .where(CameraSighting.is_animal == 1)).all()
    return {"species": sorted({n for n in names if n}), "has_unclassified": any(not n for n in names)}


@router.get("/api/cameras/activity")
def camera_activity(
    brand: list[str] = Query(default=[]),
    camera_id: list[int] = Query(default=[]),
    species: list[str] = Query(default=[]),
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    region_id: int = Depends(get_active_region_id),
    _=Depends(require_token),
):
    """Average sightings per day for each hour of the (property-local) day, over the filtered date range.
    Empty brand / camera_id / species lists mean "all"; species may include "__none__" for unclassified."""
    tz = activity.region_tz(get_region_dict(region_id)["property_timezone"])
    with Session(engine) as s:
        rows = s.execute(_scoped(select(CameraSighting.camera_id, CameraSighting.species,
                                        CameraSighting.timestamp), region_id, brand, camera_id)
                         .where(CameraSighting.is_animal == 1)).all()
    parsed = [(cid, sp or None, t) for cid, sp, ts in rows if (t := activity.parse_utc(ts)) is not None]
    recorded_since = min((t.astimezone(tz).date() for _c, _s, t in parsed), default=None)
    matching = [r for r in parsed if activity.species_matches(r[1], species)]
    return activity.hourly_activity(matching, tz=tz, today=datetime.now(tz).date(), date_from=date_from,
                                    date_to=date_to, recorded_since=recorded_since)


@router.get("/api/cameras/gallery")
def camera_gallery(
    brand: list[str] = Query(default=[]),
    camera_id: list[int] = Query(default=[]),
    species: list[str] = Query(default=[]),
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    hour_from: Optional[int] = Query(default=None, ge=0, le=24),
    hour_to: Optional[int] = Query(default=None, ge=0, le=24),
    include_empty: bool = False,
    before_ts: Optional[str] = None,
    before_id: Optional[int] = None,
    limit: int = 48,
    region_id: int = Depends(get_active_region_id),
    _=Depends(require_token),
):
    """Newest-first photo gallery across cameras. Dates and the time-of-day window are in property-local
    time (a window like 20 -> 5 wraps midnight). Page with the returned `next` cursor."""
    limit = max(1, min(limit, 200))
    tz = activity.region_tz(get_region_dict(region_id)["property_timezone"])
    lo, hi = activity.utc_date_bounds(date_from, date_to)
    chunk, items, exhausted = 200, [], False
    cursor = (before_ts, before_id) if before_ts and before_id is not None else None

    with Session(engine) as s:
        q = _scoped(select(CameraSighting, Camera), region_id, brand, camera_id)
        if not include_empty:
            q = q.where(CameraSighting.is_animal == 1)
        if species:
            named = [x for x in species if x != activity.UNCLASSIFIED]
            conds = [CameraSighting.species.in_(named)] if named else []
            if activity.UNCLASSIFIED in species:
                conds.append(CameraSighting.species.is_(None))
            q = q.where(or_(*conds))
        if lo:
            q = q.where(CameraSighting.timestamp >= lo)
        if hi:
            q = q.where(CameraSighting.timestamp < hi)
        ordered = q.order_by(CameraSighting.timestamp.desc(), CameraSighting.id.desc())

        while len(items) <= limit and not exhausted:
            page = ordered
            if cursor:
                page = page.where(or_(CameraSighting.timestamp < cursor[0],
                                      and_(CameraSighting.timestamp == cursor[0], CameraSighting.id < cursor[1])))
            batch = s.execute(page.limit(chunk)).all()
            exhausted = len(batch) < chunk
            for sg, cam in batch:
                cursor = (sg.timestamp, sg.id)
                t = activity.parse_utc(sg.timestamp)
                if t is None:
                    continue
                local = t.astimezone(tz)
                if date_from and local.date() < date_from:
                    exhausted = True          # newest-first: everything after this is older still
                    break
                if date_to and local.date() > date_to:
                    continue
                if not activity.in_hour_window(local, hour_from, hour_to):
                    continue
                items.append({**sg.to_dict(), "camera_name": cam.name, "camera_brand": cam.brand})
                if len(items) > limit:
                    break

    more = len(items) > limit
    items = items[:limit]
    return {"items": items, "next": {"ts": items[-1]["timestamp"], "id": items[-1]["id"]} if more else None}
