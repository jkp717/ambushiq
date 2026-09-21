"""Business logic for trail-camera sync, backfill, and image-directory management."""
from __future__ import annotations

import asyncio
import logging
import os
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from typing import Optional
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.cameras import detection as detection_mod
from app.cameras import providers as cameras_mod
from app.cameras.models import Camera, CameraSighting
from app.core.config import CAMERA_IMAGE_DIR
from app.core.database import engine
from app.core.security import decrypt_credentials
from app.regions.service import get_region_dict
from app.settings.service import get_settings

log = logging.getLogger(__name__)


def _sanitize_path_component(name: str) -> str:
    safe = "".join(c if c.isalnum() or c in " -_" else "_" for c in (name or "unnamed")).strip()
    return safe or "unnamed"


def get_camera_dir(brand: str, camera_name: str, region_id: int, base_dir: str | None = None) -> str:
    if not base_dir:
        settings = get_settings()
        base_dir = str(settings.get("camera_image_dir") or CAMERA_IMAGE_DIR)
    safe_brand = _sanitize_path_component(brand)
    safe_name = _sanitize_path_component(camera_name)
    return os.path.join(base_dir, f"region_{region_id}", safe_brand, safe_name)


def _to_utc_iso(ts_str: str, prop_tz_name: str) -> str:
    """Normalize a raw camera provider timestamp to a UTC ISO string.

    Camera providers may return naive local-time strings with no offset.  If no
    timezone info is present the value is treated as property local time and
    converted to UTC using the configured property_timezone.  Strings that already
    carry an explicit UTC offset (Z or ±HH:MM) are simply converted to UTC.
    Falls back to the original string if parsing fails so existing deduplication
    based on the raw string still works.
    """
    if not ts_str:
        return ts_str
    s = str(ts_str).strip()
    try:
        normalized = s[:-1] + "+00:00" if s.upper().endswith("Z") else s
        dt = datetime.fromisoformat(normalized)
        if dt.tzinfo is None:
            try:
                tz: timezone | ZoneInfo = ZoneInfo(prop_tz_name)
            except Exception:
                tz = timezone.utc
            dt = dt.replace(tzinfo=tz)  # type: ignore[arg-type]
        return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except Exception:
        return s


async def _sync_one_camera_task(camera_id: int, since_days: Optional[int] = None) -> None:
    """Background-task wrapper: sync one camera, then release the detection
    models from memory — this box has limited RAM, so we don't keep
    MegaDetector + the species classifier resident between manual syncs."""
    try:
        await _sync_one_camera(camera_id, since_days=since_days)
    finally:
        detection_mod.unload_models()



async def _backfill_species_task(sighting_ids: list[int]) -> None:
    """One-time reclassification of sightings recorded before species tracking
    existed. Only sightings whose original JPEG is still on disk (i.e. not yet
    past image_retention_days) can be reclassified — older ones already had
    their file deleted by the daily cleanup job and can't be recovered.
    Candidate ids are computed synchronously by the endpoint (below) so a bad
    query surfaces immediately in the HTTP response instead of silently
    vanishing inside a background task that never gets to its first log line."""
    log.info("backfill_species: starting on %d candidate sighting(s)", len(sighting_ids))
    reclassified = missing_file = errors = 0
    try:
        for sid in sighting_ids:
            try:
                with Session(engine) as s:
                    row = s.get(CameraSighting, sid)
                    if not row or not row.image_path:
                        continue
                    if not os.path.exists(row.image_path):
                        missing_file += 1
                        continue
                    det = await asyncio.to_thread(detection_mod.detect_animal, row.image_path)
                    if str(det.get("detector", "")).startswith("error"):
                        errors += 1
                        continue
                    if det.get("species"):
                        row.species = det.get("species")
                        row.species_confidence = det.get("species_confidence")
                        s.commit()
                        reclassified += 1
            except Exception:
                errors += 1
                log.exception("backfill_species: failed on sighting id=%s", sid)
    finally:
        detection_mod.unload_models()
    log.info("backfill_species: done — checked=%d reclassified=%d missing_file=%d errors=%d",
              len(sighting_ids), reclassified, missing_file, errors)


SYNC_OVERLAP = timedelta(days=3)     # each sync re-lists this much history so late-uploaded photos are caught
RETRY_HORIZON = timedelta(days=7)    # a photo that keeps failing to download is retried this long, then given up on


def _parse_iso(raw: Optional[str]) -> Optional[datetime]:
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None
    return (dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt).astimezone(timezone.utc)


def _photo_key(p: dict) -> Optional[str]:
    """Stable identity for a provider photo: its own id, else the host + path of its URL (the signed
    query string changes on every request, the path does not). Two photos stamped in the same second
    therefore stay distinct, which a timestamp-based check could not tell apart."""
    if p.get("id"):
        return f"id:{p['id']}"[:400]
    url = p.get("url")
    if not url:
        return None
    parts = urlsplit(url)
    return f"url:{parts.netloc}{parts.path}"[:400]


async def _sync_one_camera(camera_id: int, since_days: Optional[int] = None) -> dict:
    """Fetch recent photos for a camera, run detection, and record every photo: animal photos as
    sightings, photos with no animal kept but flagged (is_animal=0).

    Photos are fetched from `sync_cursor_at` minus SYNC_OVERLAP rather than from the last sync time:
    cellular cameras upload late, so a photo taken before the last sync can still show up after it.
    Repeats are skipped by photo identity, so the overlap costs one listing call, not re-downloads.
    The cursor only moves forward past photos that were handled; a failed download holds it back
    (up to RETRY_HORIZON) so the photo is retried. `since_days` forces a re-import of that many days.

    Returns {new, fetched, no_animal, detection_errors, failed}. Best-effort: never raises to the scheduler."""
    empty = {"new": 0, "fetched": 0, "no_animal": 0, "detection_errors": 0, "failed": 0}
    with Session(engine) as s:
        cam = s.get(Camera, camera_id)
        if not cam or not cam.is_active or cam.is_deleted:
            return empty
        creds = decrypt_credentials(cam.credentials_json)
        brand, camera_name, stand_id, cid = cam.brand, cam.name, cam.stand_id, cam.id
        region_id = cam.region_id
        cam_provider_ref = cam.provider_ref  # Spypoint cam ID for photo filtering
        cursor = _parse_iso(cam.sync_cursor_at or cam.last_sync_at)
        # What we already have for this camera, by photo identity. Rows from before photo ids were
        # stored are matched by timestamp instead and adopt the id of the photo that matches them.
        known: set[str] = set()
        legacy_by_ts: dict[str, list[int]] = defaultdict(list)
        for rid, ts, pid in s.execute(select(CameraSighting.id, CameraSighting.timestamp,
                                              CameraSighting.provider_photo_id)
                                       .where(CameraSighting.camera_id == cid)).all():
            if pid:
                known.add(pid)
            else:
                legacy_by_ts[ts].append(rid)

    # Resolve the property timezone (now a per-region field) once for taken_at
    # normalization below.
    region = get_region_dict(region_id)
    prop_tz_name = str(region.get("property_timezone") or "America/Chicago")

    sync_started = datetime.now(timezone.utc)
    backfill_days = int(get_settings().get("camera_backfill_days", 7) or 7)
    if since_days:
        since_dt = sync_started - timedelta(days=since_days)
        log.info("cam %s (%s): re-importing the last %d day(s)", camera_id, brand, since_days)
    elif cursor:
        since_dt = cursor - SYNC_OVERLAP
    else:
        # First sync — start from backfill_days ago instead of the beginning of time.
        since_dt = sync_started - timedelta(days=backfill_days)
        log.info("cam %s (%s): first sync — backfilling %d day(s)", camera_id, brand, backfill_days)

    log.info("cam %s (%s): starting sync — since=%s known=%d",
             camera_id, brand, since_dt.isoformat(), len(known) + sum(len(v) for v in legacy_by_ts.values()))

    try:
        prov = cameras_mod.get_provider(brand, creds)
        if not prov.implemented:
            log.warning("cam %s (%s): provider not implemented — skipping", camera_id, brand)
            return empty
        log.info("cam %s (%s): calling fetch_recent_photos ...", camera_id, brand)
        photos = await prov.fetch_recent_photos(since=since_dt, camera_ref=cam_provider_ref)
        log.info("cam %s (%s): provider returned %d photo(s)", camera_id, brand, len(photos))
        # Filter to only this camera's photos using provider_ref (Spypoint cam ID).
        if cam_provider_ref:
            before = len(photos)
            photos = [p for p in photos if p.get("camera_ref") == cam_provider_ref]
            log.info("cam %s (%s): filtered by provider_ref=%s: %d → %d photo(s)",
                     camera_id, brand, cam_provider_ref, before, len(photos))
    except Exception as exc:
        log.warning("cam %s (%s): fetch_recent_photos failed: %s: %s",
                    camera_id, brand, type(exc).__name__, exc)
        return empty   # cursor untouched: the next sync starts from the same place

    # Refresh provider-reported health (last check-in, photo quota) so the
    # camera-penalty health gate stays current. Best-effort — a failure here
    # must never block the actual photo sync above.
    try:
        cam_list = await prov.fetch_cameras()
        sp_cam = next((c for c in cam_list if str(c.get("id")) == str(cam_provider_ref)), None)
        if sp_cam:
            with Session(engine) as s:
                cam_row = s.get(Camera, camera_id)
                if cam_row:
                    cam_row.last_seen_at = sp_cam.get("last_seen_at")
                    cam_row.photo_count = sp_cam.get("photo_count")
                    cam_row.photo_limit = sp_cam.get("photo_limit")
                    s.commit()
    except Exception as exc:
        log.warning("cam %s (%s): health refresh failed: %s: %s",
                    camera_id, brand, type(exc).__name__, exc)

    # User-defined directory structure: [User defined directory]/region_{id}/[Camera Brand]/[Camera Name]/
    cam_dir = get_camera_dir(brand, camera_name, region_id)
    os.makedirs(cam_dir, exist_ok=True)
    new = no_animal = detection_errors = fetched = saved = 0
    failed_at: list[datetime] = []      # taken-times of photos we couldn't get, so the cursor can hold back

    def record(taken_at: str, key: Optional[str], fpath: str, *, animal: bool, conf: float,
               species: Optional[str] = None, species_conf: Optional[float] = None) -> None:
        with Session(engine) as s:
            s.add(CameraSighting(
                stand_id=stand_id, camera_id=cid, timestamp=taken_at, confidence_score=conf,
                species=species, species_confidence=species_conf, image_path=fpath,
                provider_photo_id=key, is_animal=1 if animal else 0,
                created_at=datetime.now(timezone.utc).isoformat(),
            ))
            s.commit()
        if key:
            known.add(key)

    async with httpx.AsyncClient() as client:
        for idx, p in enumerate(photos):
            url = p.get("url")
            raw_taken = p.get("taken_at")
            taken_at = _to_utc_iso(str(raw_taken), prop_tz_name) if raw_taken else None
            if not taken_at:
                log.warning("cam %s (%s): photo[%d] has no taken_at timestamp — skipped (raw=%s)", cid, brand, idx, p)
                continue
            taken_dt = _parse_iso(taken_at)
            if not url:
                log.warning("cam %s (%s): photo[%d] taken_at=%s has no URL — will retry", cid, brand, idx, taken_at)
                if taken_dt:
                    failed_at.append(taken_dt)
                continue

            key = _photo_key(p)
            if key in known:
                continue
            if legacy_by_ts.get(taken_at):
                # An older row (saved before photo ids were kept) already stands for this photo.
                rid = legacy_by_ts[taken_at].pop()
                with Session(engine) as s:
                    row = s.get(CameraSighting, rid)
                    if row:
                        row.provider_photo_id = key
                        s.commit()
                known.add(key)
                continue

            log.info("cam %s (%s): photo[%d] taken_at=%s — downloading %s", cid, brand, idx, taken_at, url[:100])
            try:
                r = await client.get(url, timeout=60)
                if r.status_code != 200:
                    log.warning("cam %s (%s): photo[%d] download HTTP %d — will retry", cid, brand, idx, r.status_code)
                    if taken_dt:
                        failed_at.append(taken_dt)
                    continue
            except Exception as exc:
                log.warning("cam %s (%s): photo[%d] download error: %s: %s — will retry",
                            cid, brand, idx, type(exc).__name__, exc)
                if taken_dt:
                    failed_at.append(taken_dt)
                continue
            fetched += 1

            saved += 1
            fname = f"cam{cid}_{int(datetime.now(timezone.utc).timestamp()*1000)}_{saved}.jpg"
            fpath = os.path.join(cam_dir, fname)
            try:
                with open(fpath, "wb") as f:
                    f.write(r.content)
            except Exception as exc:
                log.warning("cam %s (%s): photo[%d] save failed: %s: %s — will retry",
                            cid, brand, idx, type(exc).__name__, exc)
                if taken_dt:
                    failed_at.append(taken_dt)
                continue

            # offload CPU-bound ML detection to a worker thread so event loop remains non-blocking
            det = await asyncio.to_thread(detection_mod.detect_animal, fpath)
            log.info("cam %s (%s): photo[%d] detection: is_animal=%s conf=%.3f detector=%s",
                     cid, brand, idx, det.get("is_animal"), det.get("confidence", 0.0), det.get("detector"))

            if str(det.get("detector", "")).startswith("error"):
                # Detection threw (model missing, PyTorch error, bad image, ...). Record the photo at
                # confidence 0 so the user still sees it — hiding it because the detector is broken
                # would be worse than a false positive.
                detection_errors += 1
                record(taken_at, key, fpath, animal=True, conf=0.0)
                new += 1
            elif not det.get("is_animal"):
                # Nothing found: keep the photo (the detector can miss night or partial shots) but flag it
                # so it stays out of scoring and the activity graph.
                no_animal += 1
                record(taken_at, key, fpath, animal=False, conf=det.get("confidence", 0.0))
            else:
                record(taken_at, key, fpath, animal=True, conf=det.get("confidence", 0.0),
                       species=det.get("species"), species_conf=det.get("species_confidence"))
                new += 1

    log.info("cam %s (%s): sync complete — downloaded=%d animals=%d no_animal=%d det_errors=%d failed=%d",
             cid, brand, fetched, new, no_animal, detection_errors, len(failed_at))

    # Move the cursor to when this sync began — unless a download failed, in which case hold it just
    # before the earliest failure (within the retry horizon) so the next sync tries again.
    retry = [t for t in failed_at if t > sync_started - RETRY_HORIZON]
    cursor_new = (min(retry) - timedelta(seconds=1)) if retry else sync_started
    with Session(engine) as s:
        cam2 = s.get(Camera, cid)
        if cam2:
            cam2.last_sync_at = datetime.now(timezone.utc).isoformat()
            cam2.sync_cursor_at = cursor_new.isoformat()
            s.commit()

    return {"new": new, "fetched": fetched, "no_animal": no_animal,
            "detection_errors": detection_errors, "failed": len(failed_at)}
