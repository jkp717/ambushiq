"""Buck model: picks out bucks among the white-tailed deer in camera photos.

Each deer box carries a feature vector (from the species classifier's own network — see
detection.deer_features). A small logistic regression on those vectors scores how likely a deer is a
buck. It is trained only on what the user tells it:

  * the Model Training screen shows deer in batches; deer tapped are bucks, deer shown but not
    tapped are "not a buck" (implied — the user never labels does);
  * the photo viewer's Buck toggle tags a missed buck, or switches off a wrong one.

Deer the model scored but nobody looked at never train it, so it can't reinforce its own mistakes.
The model only turns on once it has MIN_EACH examples of each kind and passes MIN_CV_ACCURACY in
cross-validation; until then nothing is marked except what the user tagged. What it really sees is
antlers, so shed bucks and button bucks stay unmarked (never called does).
"""
from __future__ import annotations

import base64
import io
import json
import logging
import os
import threading
from collections import OrderedDict
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.orm import Session

from app.cameras.models import Camera, CameraSighting, DeerDetection
from app.core.config import CAMERA_IMAGE_DIR
from app.core.database import engine
from app.settings.service import get_settings

log = logging.getLogger(__name__)

MIN_EACH = 40              # bucks and not-a-buck examples needed before the model can turn on
MIN_CV_ACCURACY = 0.85     # cross-validated (balanced) accuracy it must reach to turn on
SHOW_SCORE = 0.7           # a model score at or above this marks a deer as a buck
VIEWER_RETRAIN_EVERY = 10  # viewer corrections that trigger a retrain
CV_FOLDS = 5

_train_lock = threading.Lock()
_model_cache: dict = {"mtime": None, "model": None}


# ── vectors ─────────────────────────────────────────────────────────────────────

def encode_vec(vec) -> str:
    import numpy as np
    return base64.b64encode(np.asarray(vec, dtype=np.float16).tobytes()).decode("ascii")


def decode_vec(s: str):
    import numpy as np
    return np.frombuffer(base64.b64decode(s), dtype=np.float16).astype(np.float32)


def record_detections(s: Session, sighting_id: int, detections: list[dict]) -> None:
    """Store a photo's animal boxes (and deer vectors), scoring deer when a model is on."""
    model = load_model()
    for d in detections or []:
        vec = d.get("features")
        source = d.get("feature_source")
        enc = encode_vec(vec) if vec else None
        s.add(DeerDetection(
            sighting_id=sighting_id, box_json=json.dumps(d.get("box") or [0, 0, 1, 1]),
            det_conf=float(d.get("det_conf") or 0.0), species=d.get("species"),
            vec=enc, vec_source=source if enc else None,
            buck_score=score(model, decode_vec(enc), source) if enc else None,
        ))


# ── the model ───────────────────────────────────────────────────────────────────

def _model_path() -> str:
    base = str(get_settings().get("camera_image_dir") or CAMERA_IMAGE_DIR)
    return os.path.join(base, "buck_model.json")


def load_model() -> Optional[dict]:
    path = _model_path()
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return None
    if _model_cache["mtime"] != (path, mtime):
        try:
            with open(path, encoding="utf-8") as f:
                _model_cache["model"] = json.load(f)
            _model_cache["mtime"] = (path, mtime)
        except (OSError, ValueError):
            log.warning("bucks: couldn't read %s", path, exc_info=True)
            return None
    return _model_cache["model"]


def _save_model(model: dict) -> None:
    path = _model_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(model, f)
    os.replace(tmp, path)


def score(model: Optional[dict], vec, source: Optional[str]) -> Optional[float]:
    """Buck score 0–1 for one vector, or None when no model is on (or it was built from another network)."""
    if not model or not model.get("enabled") or source != model.get("source") or vec is None:
        return None
    import numpy as np
    w = np.asarray(model["w"], dtype=np.float32)
    if vec.shape != w.shape:
        return None
    z = (vec - np.asarray(model["mean"], dtype=np.float32)) / np.asarray(model["std"], dtype=np.float32)
    return round(float(1.0 / (1.0 + np.exp(-(float(z @ w) + model["b"])))), 4)


def _fit(X, y, l2: float = 1e-2, iters: int = 400, lr: float = 0.5):
    """Class-balanced, L2-regularized logistic regression by gradient descent on standardized features."""
    import numpy as np
    mean = X.mean(axis=0)
    std = X.std(axis=0) + 1e-6
    Z = (X - mean) / std
    pos = max(1, int(y.sum()))
    neg = max(1, len(y) - pos)
    weights = np.where(y == 1, len(y) / (2 * pos), len(y) / (2 * neg)).astype(np.float32)
    w = np.zeros(Z.shape[1], dtype=np.float32)
    b = 0.0
    for _ in range(iters):
        p = 1.0 / (1.0 + np.exp(-(Z @ w + b)))
        g = weights * (p - y)
        w -= lr * ((Z.T @ g) / len(y) + l2 * w)
        b -= lr * float(g.mean())
    return w, b, mean, std


def _predict(fit, X):
    import numpy as np
    w, b, mean, std = fit
    return 1.0 / (1.0 + np.exp(-(((X - mean) / std) @ w + b)))


def _balanced_accuracy(y, p) -> float:
    import numpy as np
    pred = p >= 0.5
    tpr = float((pred[y == 1]).mean()) if (y == 1).any() else 0.0
    tnr = float((~pred[y == 0]).mean()) if (y == 0).any() else 0.0
    return (tpr + tnr) / 2


def _cv_accuracy(X, y) -> float:
    """Stratified k-fold balanced accuracy — bucks are usually far fewer than other deer."""
    import numpy as np
    rng = np.random.default_rng(0)
    folds = np.zeros(len(y), dtype=int)
    for cls in (0, 1):
        idx = np.flatnonzero(y == cls)
        rng.shuffle(idx)
        folds[idx] = np.arange(len(idx)) % CV_FOLDS
    scores = []
    for k in range(CV_FOLDS):
        test = folds == k
        if not test.any() or len(set(y[~test])) < 2:
            continue
        scores.append(_balanced_accuracy(y[test], _predict(_fit(X[~test], y[~test]), X[test])))
    return float(np.mean(scores)) if scores else 0.0


def _current_source(s: Session) -> Optional[str]:
    """The network the newest deer vectors come from — vectors from different networks don't mix."""
    return s.scalar(select(DeerDetection.vec_source).where(DeerDetection.vec.isnot(None))
                    .order_by(DeerDetection.id.desc()).limit(1))


def train(progress=None) -> dict:
    """Retrain on every labeled deer, then re-score all stored deer (or clear the scores if the
    model doesn't pass). Returns the model status."""
    import numpy as np
    with _train_lock:
        with Session(engine) as s:
            source = _current_source(s)
            rows = s.execute(select(DeerDetection.vec, DeerDetection.label).where(
                DeerDetection.vec.isnot(None), DeerDetection.vec_source == source,
                DeerDetection.label.in_(("buck", "not_buck")))).all()
        y = np.array([1 if lab == "buck" else 0 for _v, lab in rows], dtype=np.float32)
        n_buck, n_not = int(y.sum()), int(len(y) - y.sum())
        model = {"enabled": False, "source": source, "n_buck": n_buck, "n_not_buck": n_not,
                 "cv_accuracy": None, "trained_at": datetime.now(timezone.utc).isoformat()}
        if n_buck >= MIN_EACH and n_not >= MIN_EACH:
            X = np.stack([decode_vec(v) for v, _l in rows])
            acc = _cv_accuracy(X, y)
            w, b, mean, std = _fit(X, y)
            model.update(cv_accuracy=round(acc, 3), enabled=acc >= MIN_CV_ACCURACY,
                         w=[float(v) for v in w], b=float(b), mean=[float(v) for v in mean],
                         std=[float(v) for v in std])
        _save_model(model)
        _model_cache["mtime"] = None
        _rescore(load_model())
    log.info("bucks: trained — bucks=%d not_buck=%d cv=%s enabled=%s",
             n_buck, n_not, model["cv_accuracy"], model["enabled"])
    return model


def _rescore(model: Optional[dict]) -> None:
    with Session(engine) as s:
        if not model or not model.get("enabled"):
            s.execute(update(DeerDetection).values(buck_score=None))
        else:
            rows = s.execute(select(DeerDetection.id, DeerDetection.vec, DeerDetection.vec_source)
                             .where(DeerDetection.vec.isnot(None))).all()
            if rows:
                s.execute(update(DeerDetection), [
                    {"id": rid, "buck_score": score(model, decode_vec(v), src)} for rid, v, src in rows])
        s.commit()


def train_job(progress) -> str:
    """Jobs-page entry: retrain the buck model."""
    m = train(progress)
    return _summary(m)


def _summary(m: dict) -> str:
    if m["enabled"]:
        return f"on · {round(m['cv_accuracy'] * 100)}% accurate · {m['n_buck']} bucks / {m['n_not_buck']} other deer"
    if m["cv_accuracy"] is not None:
        return f"off · {round(m['cv_accuracy'] * 100)}% accurate (needs {round(MIN_CV_ACCURACY * 100)}%)"
    return f"off · {m['n_buck']} bucks / {m['n_not_buck']} other deer (needs {MIN_EACH} of each)"


# ── what the UI reads ───────────────────────────────────────────────────────────

def is_buck_sql():
    """A deer counts as a buck when tagged one, or — untagged — when the model scores it high enough."""
    return or_(DeerDetection.label == "buck",
               and_(DeerDetection.label.is_(None), DeerDetection.buck_score >= SHOW_SCORE))


def is_buck(label: Optional[str], buck_score: Optional[float]) -> bool:
    if label:
        return label == "buck"
    return buck_score is not None and buck_score >= SHOW_SCORE


def buck_sighting_ids():
    """Subquery: sightings with at least one buck — for the "Bucks only" filters."""
    return select(DeerDetection.sighting_id).where(is_buck_sql())


def _det_dict(d: DeerDetection) -> dict:
    return {"id": d.id, "box": json.loads(d.box_json), "species": d.species, "is_deer": d.vec is not None,
            "label": d.label, "buck_score": d.buck_score, "is_buck": is_buck(d.label, d.buck_score)}


def attach_detections(items: list[dict]) -> list[dict]:
    """Add each gallery item's deer (`detections`) and `buck_count`, in one query."""
    ids = [it["id"] for it in items]
    by_sighting: dict[int, list[dict]] = {i: [] for i in ids}
    if ids:
        with Session(engine) as s:
            for d in s.scalars(select(DeerDetection).where(DeerDetection.sighting_id.in_(ids))
                               .order_by(DeerDetection.det_conf.desc(), DeerDetection.id)).all():
                by_sighting[d.sighting_id].append(_det_dict(d))
    for it in items:
        dets = by_sighting.get(it["id"], [])
        it["detections"] = dets
        it["buck_count"] = sum(1 for d in dets if d["is_buck"])
    return items


def _in_region(q, region_id: int):
    """Restrict a DeerDetection query to one region's cameras (joining the sighting and camera)."""
    return (q.join(CameraSighting, CameraSighting.id == DeerDetection.sighting_id)
            .join(Camera, Camera.id == CameraSighting.camera_id)
            .where(Camera.region_id == region_id))


def get_in_region(s: Session, det_id: int, region_id: int):
    """(DeerDetection, CameraSighting, Camera) for a deer in this region, or None."""
    return s.execute(_in_region(select(DeerDetection, CameraSighting, Camera), region_id)
                     .where(DeerDetection.id == det_id)).first()


def _queue_filter(q, region_id: int):
    """Unlabeled white-tailed deer (they have a vector) on a live camera, photo not yet cleaned up."""
    return _in_region(q, region_id).where(DeerDetection.vec.isnot(None), DeerDetection.label.is_(None),
                                          CameraSighting.image_path.isnot(None), Camera.is_deleted == 0)


def _queue_query(region_id: int, model: Optional[dict]):
    q = _queue_filter(select(DeerDetection, CameraSighting, Camera), region_id)
    if model and model.get("enabled"):
        # least certain first: these teach the model the most
        return q.order_by(func.abs(func.coalesce(DeerDetection.buck_score, 0.5) - 0.5), DeerDetection.id.desc())
    return q.order_by(CameraSighting.timestamp.desc(), DeerDetection.id)


def queue(region_id: int, limit: int = 16) -> list[dict]:
    """The next deer to show in the trainer: unlabeled white-tailed deer whose photo is still on disk."""
    out: list[dict] = []
    with Session(engine) as s:
        for d, sg, cam in s.execute(_queue_query(region_id, load_model()).limit(limit * 4)).all():
            if not os.path.exists(sg.image_path):
                continue
            out.append({**_det_dict(d), "sighting_id": sg.id, "timestamp": sg.timestamp,
                        "camera_name": cam.name, "image_url": f"/api/camera-sightings/{sg.id}/image",
                        "crop_url": f"/api/deer-detections/{d.id}/crop"})
            if len(out) >= limit:
                break
    return out


def unscanned_query(region_id: int, q):
    """Restrict a CameraSighting query to this region's whitetail photos still on file that have no
    usable deer boxes: never scanned (taken before the buck model existed), or a deer whose features
    couldn't be read. "Find deer in existing photos" works through these. Only photos already
    classified as white-tailed deer are rescanned, which skips the slow detector on every raccoon and
    turkey; a deer behind another animal in an older photo is the rare miss."""
    from app.cameras.detection import DEER_SPECIES
    unreadable = select(DeerDetection.sighting_id).where(
        DeerDetection.vec.is_(None), func.lower(DeerDetection.species) == DEER_SPECIES.lower())
    scanned = select(DeerDetection.sighting_id)
    return (q.join(Camera, Camera.id == CameraSighting.camera_id)
            .where(Camera.region_id == region_id, Camera.is_deleted == 0, CameraSighting.is_animal == 1,
                   func.lower(CameraSighting.species) == DEER_SPECIES.lower(),
                   CameraSighting.image_path.isnot(None),
                   or_(CameraSighting.id.not_in(scanned), CameraSighting.id.in_(unreadable))))


def status(region_id: int) -> dict:
    from app.cameras import detection
    model = load_model() or {}
    with Session(engine) as s:
        counts = dict(s.execute(select(DeerDetection.label, func.count(DeerDetection.id))
                                .where(DeerDetection.label.isnot(None), DeerDetection.vec.isnot(None))
                                .group_by(DeerDetection.label)).all())
        waiting = s.scalar(_queue_filter(select(func.count(DeerDetection.id)), region_id))
        unscanned = s.scalar(unscanned_query(region_id, select(func.count(CameraSighting.id))))
    return {
        "available": detection.species_available(),
        "enabled": bool(model.get("enabled")), "cv_accuracy": model.get("cv_accuracy"),
        "trained_at": model.get("trained_at"),
        "n_buck": counts.get("buck", 0), "n_not_buck": counts.get("not_buck", 0),
        "waiting": waiting or 0, "unscanned": unscanned or 0,
        "min_each": MIN_EACH, "min_accuracy": MIN_CV_ACCURACY,
    }


# ── labels ──────────────────────────────────────────────────────────────────────

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def label_batch(region_id: int, shown: list[int], bucks: list[int]) -> dict:
    """Trainer batch: deer tapped are bucks, every other deer shown is not a buck. Then retrain."""
    bucks_set = set(bucks)
    with Session(engine) as s:
        for det_id in set(shown) | bucks_set:
            row = get_in_region(s, det_id, region_id)
            if not row:
                continue
            d = row[0]
            d.label = "buck" if det_id in bucks_set else "not_buck"
            d.label_source, d.labeled_at = "trainer", _now()
        s.commit()
    train()
    return status(region_id)


def set_buck(region_id: int, det_id: int, buck: bool) -> Optional[dict]:
    """Viewer toggle. On: tag as a buck. Off: a deer that was a buck (tagged, or marked by the model)
    becomes a definite "not a buck"; otherwise the tag is just cleared. Retrains every
    VIEWER_RETRAIN_EVERY corrections."""
    with Session(engine) as s:
        row = get_in_region(s, det_id, region_id)
        if not row:
            return None
        d = row[0]
        if buck:
            d.label = "buck"
        else:
            d.label = "not_buck" if is_buck(d.label, d.buck_score) else None
        d.label_source, d.labeled_at = ("viewer", _now()) if d.label else (None, None)
        s.commit()
        out = _det_dict(d)
        since = (load_model() or {}).get("trained_at") or ""
        corrections = s.scalar(select(func.count(DeerDetection.id)).where(
            DeerDetection.label_source == "viewer", DeerDetection.labeled_at > since))
    if corrections and corrections >= VIEWER_RETRAIN_EVERY:
        train()
    return out


# ── crops for the trainer ───────────────────────────────────────────────────────

_crop_cache: "OrderedDict[tuple, bytes]" = OrderedDict()
_CROP_CACHE_MAX = 256


def crop_jpeg(image_path: str, box: list[float], max_side: int = 320) -> bytes:
    """The deer's box with 15% padding, scaled to at most `max_side`, as JPEG bytes (cached)."""
    key = (image_path, tuple(box), max_side)
    if key in _crop_cache:
        _crop_cache.move_to_end(key)
        return _crop_cache[key]
    from PIL import Image
    with Image.open(image_path) as im:
        im = im.convert("RGB")
        w, h = im.size
        x1, y1, x2, y2 = box
        pw, ph = (x2 - x1) * 0.15, (y2 - y1) * 0.15
        rect = (max(0, int((x1 - pw) * w)), max(0, int((y1 - ph) * h)),
                min(w, int((x2 + pw) * w)), min(h, int((y2 + ph) * h)))
        c = im.crop(rect)
        c.thumbnail((max_side, max_side))
        buf = io.BytesIO()
        c.save(buf, format="JPEG", quality=85)
    data = buf.getvalue()
    _crop_cache[key] = data
    if len(_crop_cache) > _CROP_CACHE_MAX:
        _crop_cache.popitem(last=False)
    return data
