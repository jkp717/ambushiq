"""Business logic for trail-camera sync, backfill, and image-directory management."""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone, timedelta
from typing import Optional
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


async def _sync_one_camera_task(camera_id: int) -> None:
    """Background-task wrapper: sync one camera, then release the detection
    models from memory — this box has limited RAM, so we don't keep
    MegaDetector + the species classifier resident between manual syncs."""
    try:
        await _sync_one_camera(camera_id)
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


async def _sync_one_camera(camera_id: int) -> dict:
    """Fetch recent photos for a camera, run detection, record positive sightings.
    Returns a summary dict: {new, fetched, skipped_non_animal, detection_errors}.
    Best-effort: never raises to the scheduler."""
    with Session(engine) as s:
        cam = s.get(Camera, camera_id)
        if not cam or not cam.is_active or cam.is_deleted:
            return {"new": 0, "fetched": 0, "skipped_non_animal": 0, "detection_errors": 0}
        creds = decrypt_credentials(cam.credentials_json)
        brand, camera_name, stand_id, cid = cam.brand, cam.name, cam.stand_id, cam.id
        region_id = cam.region_id
        cam_provider_ref = cam.provider_ref  # Spypoint cam ID for photo filtering
        last_sync = cam.last_sync_at
        # Deduplication: timestamps already recorded for this camera (incl. non-animal skips)
        existing_timestamps = set(
            s.scalars(
                select(CameraSighting.timestamp).where(CameraSighting.camera_id == cid)
            ).all()
        )

    # Resolve the property timezone (now a per-region field) once for taken_at
    # normalization below.
    region = get_region_dict(region_id)
    prop_tz_name = str(region.get("property_timezone") or "America/Chicago")

    # Parse last_sync_at into a timezone-aware datetime to send as `since` to the provider.
    # First sync (last_sync_at is None): use camera_backfill_days so we fetch recent history
    # without pulling every photo ever on the account. Subsequent syncs are incremental.
    backfill_days = int(get_settings().get("camera_backfill_days", 7) or 7)
    since_dt = None
    if last_sync:
        try:
            since_dt = datetime.fromisoformat(last_sync.replace("Z", "+00:00"))
            if since_dt.tzinfo is None:
                since_dt = since_dt.replace(tzinfo=timezone.utc)
        except Exception:
            pass  # malformed timestamp; fall back to backfill window
    if since_dt is None:
        # First sync — start from backfill_days ago instead of the beginning of time.
        since_dt = datetime.now(timezone.utc) - timedelta(days=backfill_days)
        log.info("cam %s (%s): first sync — backfilling %d day(s)", camera_id, brand, backfill_days)

    log.info("cam %s (%s): starting sync — since=%s existing_ts=%d",
             camera_id, brand, since_dt.isoformat(), len(existing_timestamps))

    try:
        prov = cameras_mod.get_provider(brand, creds)
        if not prov.implemented:
            log.warning("cam %s (%s): provider not implemented — skipping", camera_id, brand)
            return {"new": 0, "fetched": 0, "skipped_non_animal": 0, "detection_errors": 0}
        log.info("cam %s (%s): calling fetch_recent_photos ...", camera_id, brand)
        photos = await prov.fetch_recent_photos(since=since_dt)
        log.info("cam %s (%s): provider returned %d photo(s)", camera_id, brand, len(photos))
        # Filter to only this camera's photos using provider_ref (Spypoint cam ID).
        # Without this, the API returns photos from ALL cameras on the account.
        if cam_provider_ref:
            before = len(photos)
            photos = [p for p in photos if p.get("camera_ref") == cam_provider_ref]
            log.info("cam %s (%s): filtered by provider_ref=%s: %d → %d photo(s)",
                     camera_id, brand, cam_provider_ref, before, len(photos))
    except Exception as exc:
        log.warning("cam %s (%s): fetch_recent_photos failed: %s: %s",
                    camera_id, brand if 'brand' in dir() else "?", type(exc).__name__, exc)
        return {"new": 0, "fetched": 0, "skipped_non_animal": 0, "detection_errors": 0}

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
    new = 0
    skipped_non_animal = 0
    detection_errors = 0
    fetched = 0
    async with httpx.AsyncClient() as client:
        for idx, p in enumerate(photos):
            url = p.get("url")
            if not url:
                log.warning("cam %s (%s): photo[%d] has no URL — skipped (raw=%s)",
                            cid, brand, idx, p)
                continue

            raw_taken = p.get("taken_at")
            taken_at = _to_utc_iso(str(raw_taken), prop_tz_name) if raw_taken else None

            if not taken_at:
                log.warning("cam %s (%s): photo[%d] url=%s has no taken_at timestamp — skipped",
                            cid, brand, idx, url[:80])
                continue

            if taken_at in existing_timestamps:
                log.debug("cam %s (%s): photo[%d] taken_at=%s already in DB — skipped",
                          cid, brand, idx, taken_at)
                continue

            log.info("cam %s (%s): photo[%d] taken_at=%s — downloading %s",
                     cid, brand, idx, taken_at, url[:100])
            fetched += 1

            # download
            try:
                r = await client.get(url, timeout=60)
                if r.status_code != 200:
                    log.warning("cam %s (%s): photo[%d] download HTTP %d — skipped",
                                cid, brand, idx, r.status_code)
                    fetched -= 1
                    continue
                log.info("cam %s (%s): photo[%d] downloaded %d bytes", cid, brand, idx, len(r.content))
            except Exception as exc:
                log.warning("cam %s (%s): photo[%d] download error: %s: %s",
                            cid, brand, idx, type(exc).__name__, exc)
                fetched -= 1
                continue

            fname = f"cam{cid}_{int(datetime.now(timezone.utc).timestamp()*1000)}_{new}.jpg"
            fpath = os.path.join(cam_dir, fname)
            try:
                with open(fpath, "wb") as f:
                    f.write(r.content)
                log.info("cam %s (%s): photo[%d] saved to %s", cid, brand, idx, fpath)
            except Exception as exc:
                log.warning("cam %s (%s): photo[%d] save failed: %s: %s",
                            cid, brand, idx, type(exc).__name__, exc)
                fetched -= 1
                continue

            # offload CPU-bound ML detection to a worker thread so event loop remains non-blocking
            log.info("cam %s (%s): photo[%d] running animal detection ...", cid, brand, idx)
            det = await asyncio.to_thread(detection_mod.detect_animal, fpath)
            log.info("cam %s (%s): photo[%d] detection result: is_animal=%s conf=%.3f detector=%s",
                     cid, brand, idx, det.get("is_animal"), det.get("confidence", 0.0), det.get("detector"))

            detector = det.get("detector", "")
            if detector.startswith("error"):
                # Detection threw an exception (model missing, PyTorch error, bad image, etc.).
                # Record the sighting at confidence 0.0 so the user can see their photo — hiding
                # it because the detector is broken would be worse than a false positive.
                log.warning("cam %s (%s): photo[%d] detector error (%s) — saving sighting at conf=0",
                            cid, brand, idx, detector)
                detection_errors += 1
                with Session(engine) as s:
                    s.add(CameraSighting(
                        stand_id=stand_id, camera_id=cid,
                        timestamp=taken_at,
                        confidence_score=0.0,
                        image_path=fpath, created_at=datetime.now(timezone.utc).isoformat(),
                    ))
                    s.commit()
                existing_timestamps.add(taken_at)
                new += 1
                continue

            if not det.get("is_animal"):
                # Detector ran successfully but found no animal — discard the file and mark
                # the timestamp as seen so this photo is never re-downloaded on the next sync.
                log.info("cam %s (%s): photo[%d] no animal detected (conf=%.3f) — discarding",
                         cid, brand, idx, det.get("confidence", 0.0))
                try:
                    os.remove(fpath)
                except OSError:
                    pass
                existing_timestamps.add(taken_at)
                skipped_non_animal += 1
                continue

            log.info("cam %s (%s): photo[%d] animal confirmed (conf=%.3f species=%s) — recording sighting",
                     cid, brand, idx, det.get("confidence", 0.0), det.get("species"))
            with Session(engine) as s:
                s.add(CameraSighting(
                    stand_id=stand_id, camera_id=cid,
                    timestamp=taken_at,
                    confidence_score=det.get("confidence", 0.0),
                    species=det.get("species"), species_confidence=det.get("species_confidence"),
                    image_path=fpath, created_at=datetime.now(timezone.utc).isoformat(),
                ))
                s.commit()
            existing_timestamps.add(taken_at)
            new += 1

    log.info("cam %s (%s): sync complete — fetched=%d new=%d non_animal=%d det_errors=%d",
             cid, brand, fetched, new, skipped_non_animal, detection_errors)

    # Record sync completion timestamp on camera
    with Session(engine) as s:
        cam2 = s.get(Camera, cid)
        if cam2:
            cam2.last_sync_at = datetime.now(timezone.utc).isoformat()
            s.commit()

    return {"new": new, "fetched": fetched,
            "skipped_non_animal": skipped_non_animal, "detection_errors": detection_errors}
