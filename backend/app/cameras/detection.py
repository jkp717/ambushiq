"""
Animal detection for trail-camera photos.

Uses MegaDetector via the PytorchWildlife package to filter camera triggers to
real animal detections. The model + PyTorch are heavy (~hundreds of MB), so the
model is LAZY-loaded on first use — the app boots fine without it.

DETECTOR_MODE env var:
  "fallback"    (default) — treat every photo as a sighting with confidence 0.0.
                            Photos always show up; no ML required. Good default
                            until you confirm the server can handle the model.
  "megadetector"          — run MegaDetector via PytorchWildlife. Requires the
                            PytorchWildlife package and a working PyTorch install.
                            On any model/dependency error the photo is still saved
                            at conf=0.0 (never silently discarded).

MEGADETECTOR_MODEL env var:
  "MDV5A"  (default) — MegaDetector v5a (lighter, well-tested)
  "MDV5B"            — MegaDetector v5b
  "MDV6-yolov10-c"   — MegaDetector v6 (heavier, more accurate)

DETECTOR_CONF env var: minimum confidence to count as a sighting (default 0.2).
"""
from __future__ import annotations
import os
import logging
import threading

_MODEL = None
_CLASSIFIER = None
_FEATURE_FALLBACK = None   # (timm model, transform) used only when DFNE's own features can't be reached
_LOCK = threading.Lock()

MAX_ANIMALS = 8            # animal boxes kept per photo (each deer gets its own buck label)
_MODE = os.environ.get("DETECTOR_MODE", "fallback")  # "megadetector" | "fallback"

log = logging.getLogger(__name__)

# ── Startup dependency pre-flight ────────────────────────────────────────────
# Runs once at import time so the very first line of `docker compose logs`
# tells you whether megadetector mode will work.
_MEGADETECTOR_DEPS = ("torch", "PytorchWildlife", "PIL", "numpy", "soundfile", "librosa")

def _preflight():
    missing = []
    for mod in _MEGADETECTOR_DEPS:
        try:
            __import__(mod)
        except ImportError:
            missing.append(mod)
    if _MODE == "fallback":
        log.info("detection: DETECTOR_MODE=fallback — skipping ML, every photo recorded")
    elif missing:
        log.warning("detection: DETECTOR_MODE=megadetector but missing deps: %s "
                    "— photos will still be saved (conf=0); set DETECTOR_MODE=fallback to silence this",
                    missing)
    else:
        log.info("detection: DETECTOR_MODE=megadetector — all deps present, model loads on first sync")

_preflight()

# MegaDetector native class IDs: 0 = animal, 1 = person, 2 = vehicle.
ANIMAL_CLASS_ID = 0
CONF_THRESHOLD = float(os.environ.get("DETECTOR_CONF", "0.2"))

# The DFNE classifier's exact label for white-tailed deer (see the CLASS_NAMES
# map in PytorchWildlife.models.classification.timm_base.DFNE). Used to decide
# which sightings should count toward a stand's trail-camera score boost.
DEER_SPECIES = "White-tailed Deer"


def is_deer(species: str | None) -> bool:
    return bool(species) and species.strip().lower() == DEER_SPECIES.lower()


def species_available() -> bool:
    """True if species classification can actually run (megadetector mode).
    In fallback mode every sighting's species is always None, so anything
    gated on a confirmed species (the camera boost/penalty) should be
    disabled entirely rather than silently treating everything as
    unconfirmed/non-deer."""
    return _MODE == "megadetector"


def _load_model():
    """Lazy-load MegaDetector via PytorchWildlife. Raises on failure."""
    global _MODEL
    if _MODEL is not None:
        return _MODEL
    with _LOCK:
        if _MODEL is not None:
            return _MODEL
        # PytorchWildlife is the current home of MegaDetector (replaces the old
        # standalone `megadetector` package). Import lazily so the app boots without it.
        from PytorchWildlife.models import detection as pw_detection  # type: ignore
        import torch  # type: ignore

        device = "cuda" if torch.cuda.is_available() else "cpu"
        model_name = os.environ.get("MEGADETECTOR_MODEL", "MDV5A")
        log.info("detection: loading MegaDetector model=%s device=%s", model_name, device)

        if model_name.upper().startswith("MDV6"):
            _MODEL = pw_detection.MegaDetectorV6(device=device, pretrained=True, version=model_name)
        else:
            # V5 variants: MDV5A (default), MDV5B
            _MODEL = pw_detection.MegaDetectorV5(device=device, pretrained=True)

        log.info("detection: model loaded")
        return _MODEL


def _load_classifier(device="cpu"):
    """Lazy-load the species classification model."""
    global _CLASSIFIER
    if _CLASSIFIER is not None:
        return _CLASSIFIER
    with _LOCK:
        if _CLASSIFIER is not None:
            return _CLASSIFIER

        from PytorchWildlife.models import classification as pw_classification

        log.info("detection: loading DFNE Classifier device=%s", device)
        _CLASSIFIER = pw_classification.DFNE(device=device)

        log.info("detection: classifier loaded")
        return _CLASSIFIER


def unload_models() -> None:
    """Release MegaDetector + the species classifier from memory.
    Both are heavy (MegaDetector is a YOLO model; the species classifier is a
    ViT-Large backbone) and this app runs on a low-RAM box with no GPU, so we
    don't keep them resident between sync batches — only load happens lazily
    when a batch actually has a new photo to process, so this is a no-op on
    ticks where nothing needed detection."""
    global _MODEL, _CLASSIFIER, _FEATURE_FALLBACK
    with _LOCK:
        if _MODEL is None and _CLASSIFIER is None and _FEATURE_FALLBACK is None:
            return
        _MODEL = None
        _CLASSIFIER = None
        _FEATURE_FALLBACK = None
    import gc
    gc.collect()
    log.info("detection: models unloaded")


def _classify(classifier, crop) -> tuple[str | None, float | None]:
    """Species + confidence for one animal crop. PytorchWildlife's TIMM-based classifiers (DFNE,
    Deepfaune) return {"prediction": ..., "confidence": ...}, not "class_name" — keyed off the actual
    `results_generation()` output shape, not assumed."""
    res = classifier.single_image_classification(crop)
    if isinstance(res, dict) and "prediction" in res:
        conf = res.get("confidence")
        return res.get("prediction"), (round(float(conf), 3) if conf is not None else None)
    return str(res), None


def _load_feature_fallback(device: str):
    """A small ImageNet backbone (convnext_tiny), used for deer feature vectors only if DFNE's own
    network can't be reached. Loaded lazily and released by unload_models like the others."""
    global _FEATURE_FALLBACK
    if _FEATURE_FALLBACK is not None:
        return _FEATURE_FALLBACK
    with _LOCK:
        if _FEATURE_FALLBACK is None:
            import timm  # type: ignore
            from timm.data import create_transform, resolve_data_config  # type: ignore
            net = timm.create_model("convnext_tiny", pretrained=True, num_classes=0).to(device).eval()
            _FEATURE_FALLBACK = (net, create_transform(**resolve_data_config({}, model=net)))
            log.info("detection: using convnext_tiny for deer feature vectors")
    return _FEATURE_FALLBACK


_feature_source_logged = False
_dfne_features_broken = False   # set once DFNE's features fail, so every later deer uses the same fallback


def _features(net, tf, pil, device: str) -> list[float]:
    import torch  # type: ignore
    with torch.no_grad():
        x = tf(pil).unsqueeze(0).to(device)
        f = net.forward_features(x)
        f = net.forward_head(f, pre_logits=True) if hasattr(net, "forward_head") else f.mean(dim=tuple(range(2, f.dim())))
    return [float(v) for v in f.flatten().cpu().numpy()]


def deer_features(classifier, crop, device: str) -> tuple[list[float] | None, str | None]:
    """A feature vector describing one deer crop, for the buck model: DFNE's own pre-classification
    features when its timm network is reachable (no extra model), else the convnext_tiny fallback —
    also used from then on if DFNE's features ever fail, so sources don't flip back and forth.
    Returns (vector, source name) — vectors from different sources aren't comparable — or (None, None)."""
    global _feature_source_logged, _dfne_features_broken
    try:
        from PIL import Image  # type: ignore
        pil = Image.fromarray(crop)
    except Exception as e:
        log.warning("detection: deer crop unreadable: %s: %s", type(e).__name__, e)
        return None, None
    net = getattr(classifier, "model", None)
    tf = getattr(classifier, "transform", None) or getattr(classifier, "transforms", None)
    if not _dfne_features_broken and net is not None and tf is not None and hasattr(net, "forward_features"):
        try:
            vec = _features(net, tf, pil, device)
            if not _feature_source_logged:
                log.info("detection: deer feature vectors from dfne")
                _feature_source_logged = True
            return vec, "dfne"
        except Exception as e:
            log.warning("detection: DFNE deer features failed (%s: %s) — switching to convnext_tiny",
                        type(e).__name__, e)
    _dfne_features_broken = True
    try:
        net, tf = _load_feature_fallback(device)
        vec = _features(net, tf, pil, device)
        if not _feature_source_logged:
            log.info("detection: deer feature vectors from convnext_tiny")
            _feature_source_logged = True
        return vec, "convnext_tiny"
    except Exception as e:
        log.warning("detection: deer feature extraction failed: %s: %s", type(e).__name__, e)
        return None, None


def detect_animal(image_path: str) -> dict:
    """
    Return {"is_animal": bool, "confidence": float, "detector": str,
            "species": str | None, "species_confidence": float | None,
            "detections": [{box, det_conf, species, species_confidence, features, feature_source}]}.

    The top-level fields describe the most confident animal (as before). `detections` lists every
    animal box (up to MAX_ANIMALS), with `box` as fractions of the image (x1, y1, x2, y2), and a
    feature vector for each white-tailed deer — used by the buck model.

    Never raises. On model failure returns is_animal=False with detector="error(...)"
    so the caller can decide what to do (currently: save at conf=0 so photos are
    never silently discarded).
    """
    if _MODE == "fallback":
        return {"is_animal": True, "confidence": 0.0, "detector": "fallback",
                "species": None, "species_confidence": None, "detections": []}

    try:
        import numpy as np          # type: ignore
        from PIL import Image       # type: ignore

        model = _load_model()

        img = np.array(Image.open(image_path).convert("RGB"))
        h, w = img.shape[:2]
        results = model.single_image_detection(img, img_path=image_path)

        # 1. Every animal box at or above the threshold, most confident first
        dets = results.get("detections")
        boxes: list[tuple[float, object]] = []
        if dets is not None and dets.confidence is not None and dets.class_id is not None:
            for i, (cls_id, conf) in enumerate(zip(dets.class_id, dets.confidence)):
                if int(cls_id) == ANIMAL_CLASS_ID:
                    boxes.append((float(conf), dets.xyxy[i]))
        boxes.sort(key=lambda b: b[0], reverse=True)
        best_conf = boxes[0][0] if boxes else 0.0

        is_animal = best_conf >= CONF_THRESHOLD
        response = {
            "is_animal": is_animal,
            "confidence": round(best_conf, 3),
            "detector": "megadetector",
            "species": None,
            "species_confidence": None,
            "detections": [],
        }

        # 2. Classify each animal crop; deer also get a feature vector for the buck model
        if is_animal:
            # Determine the device MegaDetector is currently utilizing
            device = "cuda" if "cuda" in str(getattr(model, "device", "cpu")) else "cpu"
            classifier = _load_classifier(device=device)
            for conf, box in [b for b in boxes if b[0] >= CONF_THRESHOLD][:MAX_ANIMALS]:
                x1, y1, x2, y2 = map(int, box)
                x1, y1 = max(0, x1), max(0, y1)
                crop = img[y1:y2, x1:x2]
                if crop.size == 0:
                    continue
                species, species_conf = _classify(classifier, crop)
                features, source = deer_features(classifier, crop, device) if is_deer(species) else (None, None)
                response["detections"].append({
                    "box": [round(x1 / w, 4), round(y1 / h, 4), round(min(x2, w) / w, 4), round(min(y2, h) / h, 4)],
                    "det_conf": round(conf, 3), "species": species, "species_confidence": species_conf,
                    "features": features, "feature_source": source,
                })
            if response["detections"]:
                top = response["detections"][0]
                response["species"], response["species_confidence"] = top["species"], top["species_confidence"]

        return response

    except Exception as e:
        log.warning("Detection/Classification failed on %s: %s: %s", image_path, type(e).__name__, e)
        return {"is_animal": False, "confidence": 0.0, "detector": f"error ({type(e).__name__})",
                "species": None, "species_confidence": None, "detections": []}